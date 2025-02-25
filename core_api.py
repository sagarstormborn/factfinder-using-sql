import asyncio
import threading
import websockets
from flask import Flask, jsonify, request
from flask_cors import CORS
import ast
from llama_index.core import SQLDatabase
from sqlalchemy import create_engine
from dataclasses import dataclass, field
from sqlalchemy import inspect, text
import json
from llama_index.core import PromptTemplate
import datetime
from typing import List, Optional
from sqlalchemy.exc import SQLAlchemyError
import time
from llama_index.core.workflow import (
    InputRequiredEvent,
    HumanResponseEvent,
    Workflow,
    step,
    StopEvent,
    StartEvent,
    Context,
    Event
)
from llama_index.llms.openai import OpenAI
from llama_index.llms.bedrock import Bedrock
from dotenv import load_dotenv
import os


# --------------------
# Flask Application
# --------------------
app = Flask(__name__)
CORS(app)


# --------------------
# llm init 
# --------------------
# Fetch AWS credentials from environment variables
load_dotenv()
AWS_ACCESS_KEY_ID = os.getenv("AWS_ACCESS_KEY_ID")
AWS_SECRET_ACCESS_KEY = os.getenv("AWS_SECRET_ACCESS_KEY")
AWS_REGION = os.getenv("AWS_REGION", "us-east-1")  # Default to "us-east-1" if not set
open_ai_key = os.getenv("OPENAI_API_KEY")

llm_openai_o3_200k = OpenAI(model="o3-mini-2025-01-31", api_key=open_ai_key,max_tokens=100000,temperature=0)
llm_claude_3_5_200k = Bedrock(
    model="us.anthropic.claude-3-5-sonnet-20241022-v2:0",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION,
    temperature=0,
    context_size=200000,
    max_tokens=4096,
)

llm_mistral_large_32k = Bedrock(
    model="mistral.mistral-large-2402-v1:0",
    aws_access_key_id=AWS_ACCESS_KEY_ID,
    aws_secret_access_key=AWS_SECRET_ACCESS_KEY,
    region_name=AWS_REGION,
    temperature=0,
    context_size=32000,
    max_tokens=8192,
    additional_kwargs={
        "top_p": 1.0, 
        "top_k": 50    
    }
)

# Global variables for table management
file_path_table =""
global_current_tables = []  # Current active tables
all_possible_tables = []  # All possible tables
sql_database=None
engine=None
inspector=None
#todo update this using api
domain_knowledge = """
PT_7930-PV – DDG suction pressure.  Normal range is -1.9, if suction drops to above -1.0, dryers are down.
FIC_70101-PV – FST feed.  Normal range is ~750gpm; low deviation of 20% indicates reduced rate, value of <100 FST is down.
FIC_024310B-PV – SMT feed. Normal range is ~1000gpm. Low deviation of 20% indicates reduced rate, value of <100 SMT is down.
FIC_6851-PV – Tricanter feed.  Normal range is 60gpm. Low deviation of 20% indicates reduced rate, value of <10gpm tricanter is down.
FIC_161351-PV  – Sedicanter feed.  Normal range is 160gpm, Low deviation of 20% indicates reduced rate, value of <50gpm sedicanters down.
FI_4505-PV – 190 flow to storage – Normal range is 110gpm.  Low deviation of 20% indicates reduced rate, value of <20gpm distillation down.
FIC_8401-PV – Mole sieve feed rate – Normal range is 110gpm. Low deviation of 20% indicates reduced rate, value of <20gpm Mole sieves down.
"""

 #sql database uri 
    
@dataclass
class Config:
    DB_HOST: str = "143.198.230.83"
    DB_PORT: int = 3306
    DB_NAME: str = "golgixportal"
    DB_USER: str = "golgix"
    DB_PASSWORD: str = "preciseV5"

# Load configuration
config = Config()

# database setup 
db_uri = f"mysql+pymysql://{config.DB_USER}:{config.DB_PASSWORD}@{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}"
    
# --------------------
# Rest API 
# --------------------
@app.route('/', methods=['GET'])
def home():
    return jsonify({'Status': "Running"})

@app.route('/tables', methods=['GET'])
def get_current_tables():
    global global_current_tables
    return jsonify({'current_tables': global_current_tables})

@app.route('/all_tables', methods=['GET'])
def get_all_tables():
    global all_possible_tables
    global sql_database
    # Return all possible tables with their indices
    all_possible_tables=get_table_list(sql_database)
    tables_with_index = {i: table for i, table in enumerate(all_possible_tables)}
    return jsonify({'all_tables': tables_with_index})

@app.route('/update_tables', methods=['POST'])
def update_tables():
    global global_current_tables
    try:
        # Expect a list of indices from the JSON payload
        indices = request.json.get('indices', [])
        global_current_tables = [all_possible_tables[i] for i in indices if i < len(all_possible_tables)]
        init_db()
        update_list_in_file(file_path_table, global_current_tables)
        return jsonify({'message': 'Tables updated successfully'})
    except Exception as e:
        return jsonify({'error': str(e)}), 400

def run_flask():
    # Set use_reloader=False to prevent spawning additional threads
    app.run(host='0.0.0.0', port=5000, debug=False, use_reloader=False)


# --------------------
# Helper function 
# --------------------

def read_list_from_file(filename):
    """Reads a list from a file and returns it."""
    try:
        with open(filename, 'r') as file:
            content = file.read()
            return ast.literal_eval(content)  # Safely convert string to list
    except (FileNotFoundError, SyntaxError, ValueError):
        return [] 

def update_list_in_file(filename, new_list):
    """Writes a list to a file."""
    with open(filename, 'w') as file:
        file.write(str(new_list))  
        
def init_db():
    global sql_database 
    global engine 
    global inspector
    global db_uri 
    global global_current_tables
    sql_database = SQLDatabase.from_uri(database_uri=db_uri, include_tables=global_current_tables)
    engine = sql_database.engine
    inspector = inspect(engine)
    
def get_table_list(sql_database):
    """Returns a list of all tables in the database."""
    engine = sql_database.engine  # Extract engine from SQLDatabase
    inspector = inspect(engine)
    
    return inspector.get_table_names()

def get_first_row_with_types(inspector, tables):
    result_str = ""
    
    with engine.connect() as conn:
        for table in tables:
            if table in inspector.get_table_names():
                # Get column details
                columns = inspector.get_columns(table)
                column_info = {col["name"]: str(col["type"]) for col in columns}
                
                # Get first row data
                query = text(f"SELECT * FROM `{table}` LIMIT 1")
                row = conn.execute(query).fetchone()
                
                # Format results as string
                result_str += f"Table: {table}\n"
                result_str += f"Columns and Data Types: {column_info}\n"
                result_str += f"First Row: {dict(zip(column_info.keys(), row)) if row else None}\n"
                result_str += "-" * 50 + "\n"
    
    return result_str
    
# --------------------
# Websockets Server
# --------------------
async def websocket_workflow_handler(websocket):
    # Initialize the workflow
    database_schema = get_first_row_with_types(inspector, global_current_tables)
    workflow = question_validate_wf(
        llm_openai_o3_200k,
        llm_mistral_large_32k,
        llm_claude_3_5_200k,
        database_schema,
        domain_knowledge,
        engine
    )
    handler = workflow.run()

    # Process events from the workflow
    async for event in handler.stream_events():
        if isinstance(event, CustomInputQuestion):
            # Send the prompt to the client for the main question
            await websocket.send(event.prefix)
            # Wait for client response over the websocket
            response = await websocket.recv()
            # Send the response back to the workflow
            handler.ctx.send_event(
                ValidatedQuestionEvent(response=response)
            )
        elif isinstance(event, AdditionlInfo):
            # Send the follow-up clarification prompt to the client
            await websocket.send(event.prefix)
            response = await websocket.recv()
            handler.ctx.send_event(
                RephraseQuestion(response=response)
            )
        elif isinstance(event, ProgressEvent):
            print("##########################################")
            if event.msg:  # Ensure it's not empty
                await websocket.send(str(event.msg))
                print("Sent message:", event.msg)
            else:
                print("Skipping empty progress event.")

    # Once the workflow is complete, get the final result
    final_result = await handler
    # Send the final result to the client
    await websocket.send(str(final_result))
    # Inform the client that the workflow is complete and they may send a new question
    await websocket.send("Workflow complete. Please enter your next question.")

async def echo(websocket):
    print("Websocket client connected.")
    try:
        # Continuously handle workflows on the same connection
        while True:
            await websocket_workflow_handler(websocket)
    except websockets.exceptions.ConnectionClosed as e:
        print("Websocket client disconnected.", e)

async def start_websocket_server():
    # Disable origin checking by setting origins to None and set ping settings
    async with websockets.serve(
            echo,
            "0.0.0.0",
            8765,
            origins=None,
            ping_interval=80,
            ping_timeout=20
        ):
        print("Websocket server started on ws://0.0.0.0:8765")
        await asyncio.Future()  # run forever


# --------------------
# sql agent core function 
# --------------------
CLARIFICATION_PROMPT = PromptTemplate("""\
You are a SQL optimization engineer with deep domain knowledge of industrial equipment systems. Follow this analysis process:

1. Schema Mapping:
- Identify required tables and the first row of the data from: {schema}
- Locate exact column matches for equipment IDs, timestamps, and metrics and understand the meaning based on just the first row given 
- Verify join paths using foreign keys

2. Domain Validation:
- Cross-reference {domain_knowledge} thresholds
- Confirm failure state calculations align with domain logic

3. Join Requirement Check:
- Determine if question requires combining operational metrics
- Validate time synchronization between tables if joining

For question: {question}

Additional Context:
- current data time {date}
- A SQL engine is available to execute the query and get rest of the data from the tables 
- If any information is missing or unclear, specify exactly what is needed.

Response Rules:
- Return **ONLY valid JSON** with the following structure:
  - If sufficient information:
    {{
      "sufficient": true,
      "question":  'None'"
      "Assumption":Your clear assumption
    }}
  - If insufficient information:
    {{
      "sufficient": false,
      "question":  follow up question to generate proper query
      "Assumption":Your clear assumption
    }}
""")

def check_sql_feasibility(llm, question, schema, domain_knowledge, retry=5):
    """Checks if the given question has enough information to generate SQL."""
    for i in range(retry):
      schema_str = str(schema)
      date=datetime.datetime.now()
      response = llm.complete(CLARIFICATION_PROMPT.format(
          question=question,
          schema=schema_str,
          domain_knowledge=domain_knowledge,
          date=date,
      )).text.strip()

      # Ensure the output is properly formatted JSON
      try:
          json_response = json.loads(response)
          return json_response
      except json.JSONDecodeError:
          print("there was some error to convert into json retrying")
          print(response)
          continue
    return  {
      "sufficient": None,
      "question":  None,
      "Assumption":None,
    }
    
def handle_rephrase_helper(llm, prev_question, prev_feedback, response):      
    combine_prompt = f"""
Combine the following into a single, clear to the point question:

Original Question: {prev_question}
Clarifying Question: {prev_feedback}
User's Answer: {response}

Rules:
1. Preserve the intent of the original question.
2. Incorporate the user's answer explicitly.
3. Make the question self-contained and unambiguous.
4. Return **ONLY valid JSON** in this format:
{{
    "question": "Your rephrased question here"
}}

"""
    result = llm.complete(combine_prompt).text.strip()
    return result

sql_prompt = PromptTemplate("""\
Role: Expert SQL Engineer specializing in industrial equipment systems

Task: Generate accurate SQL query following this workflow:

1. **Schema Analysis**:
- Identify required tables and its column and Use first row data to understand column meanings: {schema}
- Establish table relationships using foreign keys

2. **Domain Validation**:
- Cross-check with industry specific knowledge:
{domain_knowledge}


3. Error Avoidance:
- Previous failed queries: {prev_sql_list}
- Failure reasons: {prev_fail_reason}
- Actively avoid repeating these patterns

4. Temporal Context:
- Current system datetime: {date}

**Query Requirements**:
- Use only tables in the schema
- Prioritize index-friendly operations
- Include necessary JOINs but avoid Cartesian products
- Handle NULL values appropriately

Current Task:
Question: {question}

Response Rules:
- Return **ONLY valid JSON** with the following structure:
{{
  "query": "SELECT...",
  "rationale": "Brief technical justification"
}}

""")

# --------------------
# agent workflow 
# --------------------

# Define custom events with proper typing
class ValidatedQuestionEvent(HumanResponseEvent):
    pass

# Remove the instruction field if not needed, or make it optional
class CustomInputQuestion(InputRequiredEvent):
    # Inherits prefix: str from InputRequiredEvent
    pass  # Removed instruction field since it's not used

class AdditionlInfo(InputRequiredEvent):
    pass

class RephraseQuestion(HumanResponseEvent):
    pass

class GenerateQueryWithRetry(Event):
    pass 

class ProgressEvent(Event):
    msg: str

class question_validate_wf(Workflow):
    def __init__(self, llm1,llm2,llm3, schema, domain_knowledge,engine,debug=True):
        # Remove the self.ctx.set() call from here
        super().__init__(timeout=600, verbose=True)
        self.llm1 = llm1
        self.llm2 = llm2
        self.llm3= llm3
        self.schema = schema
        self.domain_knowledge = domain_knowledge
        self.debug=debug
        self.engine=engine
    @step
    async def get_question(self, ev: StartEvent) -> CustomInputQuestion:
        return CustomInputQuestion(prefix="Enter your question: ")  # Now valid
    
    @step
    async def validate_question(self,ctx: Context, ev: ValidatedQuestionEvent) -> GenerateQueryWithRetry|AdditionlInfo:
        if self.debug:
            print("question :",ev.response)
        await ctx.set("original_question",ev.response)
        # print("schema :",self.schema)
        # print("domain :",self.domain_knowledge)
        response_json=check_sql_feasibility(self.llm1, question=ev.response, schema=self.schema, domain_knowledge=self.domain_knowledge)
        print(type(response_json["sufficient"]),response_json["sufficient"])
        await ctx.set("sufficient",response_json["sufficient"])
        await ctx.set("Assumption",response_json["Assumption"])
        await ctx.set("Follow_up_Question",response_json["question"])
        ctx.write_event_to_stream(ProgressEvent(msg=f""))
        if response_json["sufficient"]:
            if self.debug:
                print("Enough data to create sql query")
            ctx.write_event_to_stream(ProgressEvent(msg=f"The question contains enough details to generate a response."))
            p_a=response_json["Assumption"]
            ctx.write_event_to_stream(ProgressEvent(msg=f"Assumption {p_a}"))
            return GenerateQueryWithRetry()
        else :
            if self.debug:
                print("Not enough data to create sql query follow up question")
            ctx.write_event_to_stream(ProgressEvent(msg=f"The question lacks sufficient information to generate a response and requires a follow-up question."))
            return AdditionlInfo(prefix=response_json["question"])
        
    @step
    async def handle_rephrase(self, ctx: Context,ev: RephraseQuestion) -> ValidatedQuestionEvent :
        # if self.debug:
        #     print("question :",ev.response)
        prev_question = await ctx.get("original_question")
        prev_feedback = await ctx.get("Follow_up_Question")
        
        rephrase_que = json.loads(handle_rephrase_helper(
            llm=self.llm2,
            prev_question=prev_question,
            prev_feedback=prev_feedback,
            response=ev.response,
        ))
        # if self.debug:
        #     print("rephrased question :",rephrase_que['question'])
        ctx.write_event_to_stream(ProgressEvent(msg=f"Rephrased question : {rephrase_que['question']}"))
        return ValidatedQuestionEvent(response=rephrase_que['question'])
    
    @step
    async def Generate_Final_Ans(self, ctx: Context,ev:GenerateQueryWithRetry) -> StopEvent:
        sql_prompt = PromptTemplate("""\
        Role: Expert SQL Engineer specializing in industrial equipment systems

        Task: Generate accurate SQL query following this workflow:

        1. **Schema Analysis**:
        - Identify required tables and its column and Use first row data to understand column meanings: {schema}
        - Establish table relationships using foreign keys

        2. **Domain Validation**:
        - Cross-check with industry specific knowledge:
        {domain_knowledge}


        3. Error Avoidance:
        - Previous failed queries: {prev_sql_list}
        - Failure reasons: {prev_fail_reason}
        - Actively avoid repeating these patterns

        4. Temporal Context:
        - Current system datetime: {date}

        **Query Requirements**:
        - Use only tables in the schema
        - Prioritize index-friendly operations
        - Include necessary JOINs but avoid Cartesian products
        - Handle NULL values appropriately

        Current Task:
        Question: {question}

        Response Rules:
        - Return **ONLY valid JSON** with the following structure:
        {{
        "query": "SELECT...",
        "rationale": "Brief technical justification"
        }}

        """)

        def generate_sql_query(
            llm_engine,
            engine,
            question: str,
            schema: str,
            domain_knowledge: str,
            prev_sql_list: Optional[List[str]] = None,
            prev_fail_reason: Optional[List[str]] = None,
            date: str = datetime.datetime.now(),
            retry:int=4
        ) -> dict:
            """
            Generates SQL query using industrial equipment database schema
            """
            # Format previous attempts for prompt
            prev_sql_str = "\n- ".join(prev_sql_list) if prev_sql_list else "None"
            prev_fail_str = "\n- ".join(prev_fail_reason) if prev_fail_reason else "None"

            formatted_prompt = sql_prompt.format(
                question=question,
                schema=schema,
                domain_knowledge=domain_knowledge,
                prev_sql_list=str(prev_sql_str),
                prev_fail_reason=str(prev_fail_str),
                date=date,
            )
            # print(formatted_prompt)
            query=json.loads(llm_engine.complete(formatted_prompt).text.strip())['query']
            
            # print("query generated")
            # print(query)
            ctx.write_event_to_stream(ProgressEvent(msg=f"trying to generate query"))
            
            for attempt in range(retry):
                try:
                    with engine.connect() as conn:
                        # Safety check
                        if any(keyword in query.upper() for keyword in [" DROP ", "DELETE", "UPDATE", "INSERT"]):
                            prev_sql_list.append(query)
                            prev_fail_reason.append("""Modification queries are blocked" DROP ", "DELETE", "UPDATE", "INSERT" """ )
                            
                            # Regenerate prompt with updated attempts
                            prev_sql_str = "\n- ".join(prev_sql_list)
                            prev_fail_str = "\n- ".join(prev_fail_reason)
                            formatted_prompt = sql_prompt.format(
                                question=question,
                                schema=schema,
                                domain_knowledge=domain_knowledge,
                                prev_sql_list=prev_sql_str,
                                prev_fail_reason=prev_fail_str,
                                date=date,
                            )
                            # Regenerate query
                            try:
                                response = llm_engine.complete(formatted_prompt).text.strip()
                                # print(response)
                                query = json.loads(response)['query']
                            except (json.JSONDecodeError, KeyError) as e:
                                continue
                            raise ValueError("Modification queries are blocked")
                        
                        # Execute and fetch results
                        cursor = conn.execute(text(query))
                        results = cursor.fetchall()
                        ctx.write_event_to_stream(ProgressEvent(msg=f"Query : {query}"))
                        ctx.write_event_to_stream(ProgressEvent(msg=f"Result : {results}"))
                        print(results)
                        return {
                            "query": query,
                            "results": results,
                            "error": None,
                            "question": question
                        }
                except (SQLAlchemyError, ValueError) as e:
                    print(f"retrying {type(e).__name__}: {str(e)}")
                    error_msg = f"{type(e).__name__}: {str(e)}"
                    prev_sql_list.append(query)
                    prev_fail_reason.append(error_msg)
                    
                    # Regenerate prompt with updated attempts
                    prev_sql_str = "\n- ".join(prev_sql_list)
                    prev_fail_str = "\n- ".join(prev_fail_reason)
                    formatted_prompt = sql_prompt.format(
                        question=question,
                        schema=schema,
                        domain_knowledge=domain_knowledge,
                        prev_sql_list=prev_sql_str,
                        prev_fail_reason=prev_fail_str,
                        date=date,
                    )
                    # Regenerate query
                    try:
                        response = llm_engine.complete(formatted_prompt).text.strip()
                        # print(response)
                        query = json.loads(response)['query']
                    except (json.JSONDecodeError, KeyError) as e:
                        continue
            
            return {"error": f"Failed after {retry} retries"}
        result_sql_query = generate_sql_query(
            llm_engine=self.llm1,
            engine=engine,
            question= await ctx.get("original_question"),
            schema=self.schema,
            domain_knowledge=self.domain_knowledge,
            prev_sql_list=[],  # From previous attempts
            prev_fail_reason=[],  # Previous errors
        )
        # print(result_sql_query['results'])
        
        def final_ans(llm, question: str, domain_knowledge: str, result: str, sql_query:str,data_schema:str, chunk_size: int = 200000):
            """
            Generates a final LLM output by interpreting the SQL result table in chunks.
            It does not perform any calculations but provides context and insights.
            """
            
            interpretation_prompt = PromptTemplate("""
            Role: Data Analyst specializing in industrial equipment systems.

            Task: Interpret the given SQL result table based on domain-specific knowledge.
            
            **Context:**
            - Industry-specific knowledge: {domain_knowledge}
            - User question: {question}
            - database Schema :{data_schema}
            - SQL query : {sql_query}
            - SQL query result table:
            ```
            {result_chunk}
            ```

            **Response Guidelines:**
            - Summarize the key insights from the result.
            - Do NOT perform any calculations or draw conclusions.
            - Present observations neutrally, allowing the user to decide further actions.
            - Maintain clarity and conciseness.
            - Do not give wage ans make sure the unit is right
            - if greeting question reply with just greeting

            Response:
            """)
            
            responses = []
            
            for i in range(0, len(result), chunk_size):
                chunk = result[i:i+chunk_size]
                formatted_prompt = interpretation_prompt.format(
                    question=question,
                    domain_knowledge=domain_knowledge,
                    result_chunk=chunk,
                    data_schema=data_schema,
                    sql_query=sql_query
                )
                response = llm.complete(formatted_prompt).text.strip()
                responses.append(response)
                ctx.write_event_to_stream(ProgressEvent(msg=f"intermediate Summary : {response}"))
                print("intermediate response")
                print(response)
            
            consolidated_prompt = PromptTemplate("""
            Role: Data Analyst specializing in industrial equipment systems.

            Task: Summarize the insights from multiple interpretations of the SQL result table.
            
            **Context:**
            - Industry-specific knowledge: {domain_knowledge}
            - User question: {question}
            - Extracted insights from chunks:
            ```
            {final_response}
            ```

            **Response Guidelines:**
            - Provide a clear and structured summary of the insights.
            - Do NOT perform any calculations or draw conclusions.
            - Present observations neutrally, allowing the user to decide further actions.
            - Maintain clarity and conciseness.
            - Do not give wage ans make sure the unit is right
            - if greeting question reply with just greeting

            Response:
            """)
            
            final_response = "\n".join(responses)
            formatted_consolidated_prompt = consolidated_prompt.format(
                question=question,
                domain_knowledge=domain_knowledge,
                final_response=final_response
            )
            ctx.write_event_to_stream(ProgressEvent(msg=f"Final Summary"))
            consolidated_response = llm.complete(formatted_consolidated_prompt).text.strip()
            
            return consolidated_response
        conclusion=final_ans(self.llm3,await ctx.get("original_question"),self.domain_knowledge,str(result_sql_query['results']),sql_query=result_sql_query['query'],data_schema=self.schema)
        return StopEvent(conclusion)
        
        
# --------------------
# Main entry point
# --------------------
if __name__ == '__main__':
    
    # init database 
    init_db()
    
    # Start Flask in a separate thread
    file_path_table = '/home/sagar/Desktop/Coding/my_github/factfinder-using-sql/default_table.txt'
    global_current_tables=read_list_from_file(file_path_table)
    flask_thread = threading.Thread(target=run_flask)
    flask_thread.start()
    
    # Run the websockets server in the main asyncio event loop
    asyncio.run(start_websocket_server())



#  websocat -n -E -v ws://localhost:8765/
#   curl -X POST http://localhost:5000/update_tables      -H "Content-Type: application/json"      -d '{"indices": [0, 2, 4]}'
#   curl http://localhost:5000/tables
#   curl http://localhost:5000/
#   curl http://localhost:5000/all_tables


# websocat -n -E -v ws://103.227.96.222:8765/
# curl -X POST http://103.227.96.222:5000/update_tables      -H "Content-Type: application/json"      -d '{"indices": [0, 2, 4]}'
# curl http://103.227.96.222:5000/tables
# curl http://103.227.96.222:5000/
# curl http://103.227.96.222:5000/all_tables


# sample question
# How is Fermentation doing, % ethanol, % total sugars, % lactic acid, % acetic acid, pH and temperature in last batch