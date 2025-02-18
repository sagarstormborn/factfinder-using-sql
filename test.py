from llama_index.llms.ollama import Ollama
from llama_index.core.tools import BaseTool, FunctionTool
from llama_index.core.agent import ReActAgent
from llama_index.core import SQLDatabase, PromptTemplate
from sqlalchemy import create_engine
from dataclasses import dataclass, field
import asyncio
from typing import Dict, Any, Tuple, List

# Initialize the model
llm1 = Ollama(model="qwen2.5:72b", request_timeout=300.0)

# Memory Management
_memory = ""

def current_memory_function() -> str:
    """Returns the current global memory string."""
    return _memory

def update_memory(new_info: str) -> None:
    """Updates the global memory with new information if not already present."""
    global _memory
    entries = _memory.split('\n') if _memory else []
    if new_info.strip() and new_info not in entries:
        entries.append(new_info)
        _memory = '\n'.join(entries)

# SQL Database Configuration
@dataclass
class Config:
    DB_HOST: str = "143.198.230.83"
    DB_PORT: int = 3306
    DB_NAME: str = "golgixportal"
    DB_USER: str = "golgix"
    DB_PASSWORD: str = "preciseV5"
    ALLOWED_TABLES: List[str] = field(default_factory=lambda: [
        "DE", "HPLC_Data", "fermentation_data", 
        "plc_data_1", "plc_data_2", "plc_data_3", "plc_data_4"
    ])

config = Config()
db_uri = f"mysql+pymysql://{config.DB_USER}:{config.DB_PASSWORD}@{config.DB_HOST}:{config.DB_PORT}/{config.DB_NAME}"
sql_database = SQLDatabase.from_uri(db_uri, include_tables=config.ALLOWED_TABLES)

# SQL Query Engine
from llama_index.core.query_engine import NLSQLTableQueryEngine
from llama_index.core.tools import QueryEngineTool

sql_query_engine = NLSQLTableQueryEngine(
    sql_database=sql_database,
    llm=llm1,
    embed_model='local',
    verbose=True
)
sql_tool = QueryEngineTool.from_defaults(
    query_engine=sql_query_engine,
    name="sql_tool",
    description="Useful for translating a natural language query into a SQL query"
)

# Memory and SQL interaction tools
memory_tool = FunctionTool.from_defaults(fn=current_memory_function)
update_memory_tool = FunctionTool.from_defaults(fn=update_memory)

# Human-in-the-Loop Function
async def ask_user_for_clarification(question: str) -> str:
    print(f"Need clarification: {question}")
    return input("Please provide clarification: ")

# Query Processing with Human Loop
async def process_query(query: str):
    # Check if we can answer the query with current knowledge
    memory = current_memory_function()
    if "relevant information" not in memory:
        clarification = await ask_user_for_clarification("Can you provide more details for this query?")
        update_memory(clarification)

    # Try to generate SQL query
    try:
        response = await sql_query_engine.aquery(query)
        inferred_sql = response.metadata.get('sql_query')
        
        # Validate SQL query with human input if necessary
        validation_prompt = PromptTemplate(
            "Is this SQL query correct for the query '{query}'?\n\nSQL Query: {sql}\n\nAnswer YES or NO."
        )
        validation_query = validation_prompt.format(query=query, sql=inferred_sql)
        validation_response = await llm1.acomplete(validation_query)
        
        if "NO" in validation_response.text.upper():
            raise ValueError("SQL query validation failed.")
        
        print(f"SQL Query: {inferred_sql}")
        print(f"Response: {response}")

    except Exception as e:
        print(f"Error in query processing: {e}")
        clarification = await ask_user_for_clarification("Query processing failed. Can you clarify your question?")
        update_memory(clarification)
        # Retry with updated context
        return await process_query(query)
    
    # Final validation of answer
    final_validation_prompt = PromptTemplate(
        "Does this answer '{response}' correctly answer the query '{query}'?\n\nAnswer YES or NO."
    )
    final_validation_query = final_validation_prompt.format(query=query, response=str(response))
    final_validation_response = await llm1.acomplete(final_validation_query)

    if "NO" in final_validation_response.text.upper():
        print("I cannot answer this query correctly with the current information.")
    else:
        print(f"Answer: {response}")

# Example usage
if __name__ == "__main__":
    asyncio.run(process_query("Downtime on equipment last 24 hours ?"))