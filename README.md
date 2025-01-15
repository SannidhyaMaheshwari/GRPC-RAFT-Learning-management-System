
# Distrubuted Learning Mangement system - RAFT protocol , LLM

Implementing a distributed Learning Management System (LMS ) with Raft -based consensus for data consistency . It involves fault
tolerance , leader election , log replication , and RPC communication using gRPC .LLM (flan-T5 base )is used to answer the query
depending on the data provided.


## Run on multiple systems or locally
update config.conf file accordingly
<br/>
Clone the project

```bash
  git clone https://github.com/SannidhyaMaheshwari/GRPC-RAFT-Learning-management-System.git
```

to compile proto file

```bash
  python -m grpc_tools.protoc -I. --python_out=. --grpc_python_out=. <name>.proto

```

to run server - 


```bash
  python lms_server.py <server no>
```

to run client - 

```bash
  python lms_client.py
```

note - you may need to install grpc and other dependencies and running server first time may take time because of LLM.

