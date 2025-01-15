Distrubuted Learning Mangement system - Distributed System , LLM

Implementing a distributed Learning Management System (LMS ) with Raft -based consensus for data consistency . It involves fault
tolerance , leader election , log replication , and RPC communication using gRPC .LLM (flan-T5 base )is used to answer the query
depending on the data provided .

to compile proto file - 
python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. \<name\>.proto

to run server - 
python lms_server.py \<server no\>

to run client -
python lms_client.py 
