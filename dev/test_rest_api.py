from flask import Flask, jsonify, request
from flask_cors import CORS

app = Flask(__name__)
CORS(app)

# Global variables
global_current_tables = ['a', 'b', 'c']  # Current active tables
all_possible_tables = ['table1', 'table2', 'table3', 'table4', 'table5']  # All possible tables

@app.route('/', methods=['GET'])
def home():
    if(request.method == 'GET'):
        return jsonify({'Status': "Running"})

@app.route('/tables', methods=['GET'])
def get_current_tables():
    return jsonify({'current_tables': global_current_tables})

@app.route('/all_tables', methods=['GET'])
def get_all_tables():
    # Return all possible tables with their indices
    tables_with_index = {i: table for i, table in enumerate(all_possible_tables)}
    return jsonify({'all_tables': tables_with_index})

@app.route('/update_tables', methods=['POST'])
def update_tables():
    global global_current_tables
    try:
        # Expect a list of indices
        indices = request.json.get('indices', [])
        # Update global_current_tables based on indices
        global_current_tables = [all_possible_tables[i] for i in indices if i < len(all_possible_tables)]
        return jsonify({
            'message': 'Tables updated successfully',
            # 'current_tables': global_current_tables
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 400

if __name__ == '__main__':
    host_ip = '0.0.0.0'
    port_no = 5000
    app.run(host=host_ip, port=port_no, debug=True)
    
# test 
# curl http://localhost:5000/all_tables
# curl http://localhost:5000/
# curl http://localhost:5000/tables
# curl -X POST http://localhost:5000/update_tables      -H "Content-Type: application/json"      -d '{"indices": [0, 2, 4]}'

