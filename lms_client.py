import os
import grpc
from requests import session
import lms_pb2
import lms_pb2_grpc
import json
import time

# Function to read server list from config file
def load_server_list(config_file="config.conf"):
    server_list = []
    try:
        with open(config_file, 'r') as file:
            for line in file:
                # Parsing each line in the format: id ip_address port
                parts = line.strip().split()
                if len(parts) == 3:
                    server_id, ip_address, port = parts
                    server_list.append(f"{ip_address}:{port}")
    except FileNotFoundError:
        print(f"Configuration file {config_file} not found.")
    return server_list

def connect_to_leader(retry=True, config_file="config.conf"):
    """
    Tries to connect to the leader and returns a stub for gRPC interaction.
    If no leader is found, it will keep retrying with linear backoff if retry is set to True.
    """
    global leader_ip_addr_port_num
    attempt = 0
    backoff = 1
    server_list = load_server_list(config_file)

    if not server_list:
        print("No servers available in config file.")
        return None

    while True:
        for server in server_list:
            try:
                channel = grpc.insecure_channel(server)
                stub = lms_pb2_grpc.LMSServiceStub(channel)

                params = lms_pb2.Empty()
                response = stub.GetLeader(params)

                if response.nodeId == -1:
                    print(f"No leader found on {server}. Trying next server...")
                else:
                    leader_ip_addr_port_num = response.nodeAddress
                    print(f"Leader found: Node {response.nodeId} at {leader_ip_addr_port_num}")

                    leader_channel = grpc.insecure_channel(leader_ip_addr_port_num)
                    leader_stub = lms_pb2_grpc.LMSServiceStub(leader_channel)   

                    return leader_stub  # Return the stub for leader interactions
            except grpc.RpcError as e:
                print(f"Error connecting to {server}: {e}. Trying next server...")

        # If no leader was found after trying all servers
        if not retry:
            print("No leader found and retry is disabled.")
            return None

        # Increment the retry attempt and apply linear backoff
        attempt += 1
        print(f"Leader not found. Retrying in {backoff} seconds... (Attempt {attempt})")
        time.sleep(backoff)
        # The backoff remains constant or could increase linearly, e.g., by 5 seconds
        backoff += 1

def handle_rpc_errors(function):
    """Decorator to handle RPC errors and attempt reconnection to a new leader."""
    def wrapper(*args, **kwargs):
        global leader_ip_addr_port_num
        try:
            return function(*args, **kwargs)
        except grpc.RpcError:
            print("Leader is unavailable, attempting to reconnect to a new leader...")
            stub = connect_to_leader()
            if stub:
                return function(stub, *args[1:], **kwargs)  # Retry the function with the new leader
            else:
                print("Failed to reconnect to a leader.")
                return None
    return wrapper

@handle_rpc_errors
def menu():
    print("\n---- LMS Client Menu ----")
    print("1. Logout")
    print("2. Ask Queries")
    print("3. Get Course Materials")
    print("4. Upload Assignment")
    print("5. Get Assignments")
    print("6. see grades")
    print("7. Ask LLM")
    return input("Select an option: ")

@handle_rpc_errors
def login(stub, role):
    username = input("Enter username: ")
    password = input("Enter password: ")
    #print (role)
    response = stub.login(lms_pb2.LoginRequest(username=username, password=password, role= role))
    if response.status == True:
        print(f"Login successful. Token: genrated")
        return response
    else:
        print("Login failed." + response.message)
        return None

@handle_rpc_errors
def logout(stub, token):
    if token:
        response = stub.logout(lms_pb2.LogoutRequest(token=token))
        if response.status == True:
            print("Logout successful.")
            return None
        else:
            print("Logout failed.")
    else:
        print("You are not logged in.")
    return token

# @handle_rpc_errors
# def replicate_file_to_followers(stub, filepath, filename, token, type):
#     if not os.path.exists(filepath):
#         print("file does not exists")
#         return

#     upload_path = os.path.join(filepath, filename)
#     def file_chunks():
#         with open(upload_path, 'rb') as f:
#             while chunk:= f.read(4096):
#                 yield lms_pb2.FileChunk(content=chunk, filename=filename, token=token, type=type)
    
#     response = stub.ReplicateFile(file_chunks())
#     print(f"Successful replication {response:message}")



@handle_rpc_errors
def upload_file(stub, filename ,path ,token ,type):
    #upload from path client side
    if not os.path.exists(path):
        print("file does not exist upload_file client" + path)
        return 
    
    upload_path = os.path.join(path, filename)
    def file_chunks():
        with open(upload_path, 'rb') as f:
            while chunk := f.read(4096):
                yield lms_pb2.FileChunk(content=chunk, filename=filename,token=token ,type=type)

    response = stub.UploadFile(file_chunks())
    print(f"Upload status: {response.message}")

@handle_rpc_errors
def get_server_addresses():
    servers = []
    with open("config.conf", "r") as config:
        for line in config:
            # Assuming each line is formatted as "id IP port"
            _, ip, port = line.strip().split()
            servers.append(f"{ip}:{port}")
    return servers



@handle_rpc_errors
def download_file(stub, filename ,path ,token ,type):
    #path where to download on client side
    request = lms_pb2.FileRequest(filename=filename ,token=token ,type=type, path=path)
    response = stub.DownloadFile(request)
    if not os.path.exists(path):
        os.makedirs(path)

    download_path = os.path.join(path, filename)
    #download to ?

    with open(download_path, "wb") as f:
        for file_chunk in response:
            f.write(file_chunk.content)

    print(f"File downloaded successfully to {download_path}")



@handle_rpc_errors
def listReq(stub , token , type, path):
    #print("list")
    response = stub.listReq(lms_pb2.listRequest(token = token , type = type , path = path)) 
    if(type == 's/q' or type == "i/g" or type == 's/g' or type =="i/s"):
        return response.lst
    l = response.lst.split("##")
    #print(l , "yes" )
    for i in l :
        print(i)
    return 


@handle_rpc_errors
def queries(stub, token, type):
    
    q = listReq(stub, token, type, "")
    
    
    queries_dict = json.loads(q) 

    #Display all the queres to the student
    print("Here are the available queries:")
    for idx, query in enumerate(queries_dict.keys(), 1):
        print(f"{idx}. {query}")
    
    # Ask the student which query they want to view
    try:
        query_num = int(input("Enter the query number you want to view: "))
        if 1 <= query_num <= len(queries_dict):
            selected_query = list(queries_dict.keys())[query_num - 1]
            # Display the response to the selected query
            print(f"Response: {queries_dict[selected_query]}")
        else:
            print("Invalid query number. Please enter a valid number.")
    except ValueError:
        print("Please enter a valid number.")


@handle_rpc_errors
def grading(stub ,token):
    d = listReq(stub , token , "i/g" ,"")
    d = json.loads(d)
    for idx, f in enumerate(d.keys(), 1):
        print(f"{idx}. {f}  -  {d[f]}")
    filename = ""
    no = ""
    try:
        query_num = int(input("Enter the file NUMBER you want to grade  / or want to modify current grade"))
        no = input("enter grade you want to give (A+,A,B+,B,C+,C)")
        if 1 <= query_num <= len(d):
            filename = list(d.keys())[query_num - 1]
            # Display the response to the selected query
            res = stub.grade(lms_pb2.gradeReq(token = token , username = "" , filename = filename , no = no))
            if res.status == True:
                print(f"sucsessfully given grade {no} to {filename}")
            #print(f"sucsessfully given grade {no} to {filename}")
        else:
            print("Invalid query number. Please enter a valid number.")
            return
        
        
    except ValueError:
        print("### Please enter a valid number.")


@handle_rpc_errors    
def seeGrades(stub ,token):
    d = listReq(stub=stub ,token=token , type = "s/g" , path = "")
    d = json.loads(d)
    for idx, f in enumerate(d.keys(), 1):
        print(f"{idx}. {f}  -  {d[f]}")


@handle_rpc_errors
def lststudent(stub ,token):
    d = listReq(stub=stub ,token=token , type = "i/s" , path = "")
    d = json.loads(d)
    for i in d:
        print(i)


@handle_rpc_errors
def llmq (stub ,token , type , que, file):
    res = stub.llmq(lms_pb2.llmreq(token = token , type = type , quetion = que ,filename = file)) 
    print(res.ans)


@handle_rpc_errors
def server_failure():
    print(f"The server {server_ip_addr_port_num} is unavailable")
    return


@handle_rpc_errors
# Parse user input from command line
def parse_user_input(message):
    command = message.split(" ")[0]
    parsed_message = message.split(" ")

    if command == "connect":
        return ("Connect", parsed_message[1])
    elif command == "getleader":
        return ("GetLeader", "GetLeader")
    elif command == "suspend":
        return ("Suspend", int(parsed_message[1]))
    elif command == "setval":
        return ("SetVal", parsed_message[1], parsed_message[2])
    elif command == "getval":
        return ("GetVal", parsed_message[1])
    elif command == "quit":
        return ("Quit", "quit")
    else:
        return ("Invalid", "invalid")

@handle_rpc_errors
def connect(ip_addr_port_num):

    global server_ip_addr_port_num

    server_ip_addr_port_num = ip_addr_port_num

# Issue a GetLeader command to the server

@handle_rpc_errors
def get_leader():
    channel = grpc.insecure_channel(server_ip_addr_port_num)
    stub = lms_pb2_grpc.LMSServiceStub(channel)

    try:
        params = lms_pb2.Empty()
        response = stub.GetLeader(params)
    except grpc.RpcError:
        server_failure()
        return

    if response.nodeId == -1:
        print("No leader. This node hasn't voted for anyone in this term yet...")
    
    else:
        print(f"{response.nodeId} {response.nodeAddress}")

# Issue a Suspend command to the server
def suspend(period):
    channel = grpc.insecure_channel(server_ip_addr_port_num)
    stub = lms_pb2_grpc.LMSServiceStub(channel)

    try:
        params = lms_pb2.SuspendRequest(period=period)
        response = stub.Suspend(params)
    except grpc.RpcError:
            server_failure()
            return

@handle_rpc_errors
def run():
    # channel = grpc.insecure_channel('localhost:50051')
    # stub = lms_pb2_grpc.LMSServiceStub(channel)
    token = None

    while True:
        print("1) Login")
        print("2) Exit")
        choice = input("Enter choice: ")

        if choice == "1":
                stub = connect_to_leader()

                if stub is None:
                    print("Could not connect to any leader.")
                    continue

                role = input("Enter role (1-student/2-instructor): ")
                response = login(stub, role)

                if response.status:
                    token = response.token
                    

                    print(f"Logged in successfully")
                    
                    while True:
                        #stdent
                        if role == "1":
                            option = menu()

                            if option == '1':
                                token = logout(stub, token)
                                break
                            elif option == '2':
                                queries(stub, token, "s/q")
                            elif option == '3':

                                listReq(stub, token, "s/cm", "")
                                file_name = input("\nEnter the file Name to download - ")
                                download_file(stub, file_name, "student_download/", token, "s/cm")

                            elif option == '4':
                                files = [f for f in os.listdir("student_upload") if os.path.isfile(os.path.join("student_upload",f))]
                                print("files are  - " )
                                for i in files:
                                    print(i)
                                file_name = input("Enter the file Name to upload - ")
                                upload_file(stub, file_name, "student_upload/" , token ,"s/ua")
                            elif option == '5':
                                listReq(stub, token, "s/as", "")
                                file_name = input("enter the file to download - ")
                                download_file(stub, file_name, "student_download/", token, "s/as")
                            elif option == '6':
                                seeGrades(stub ,token)
                            elif option == '7':
                                print("1. ask about course")
                                print("2. ask about assignments")
                                print("3. ask in general questions")
                                print("4. ask about grades")
                                ans = input("Enter your choice - ")
                                if ans=='1':
                                    listReq(stub, token, "s/cm", "")
                                    file_name = input("enter the file to ask about - ")
                                    question = input("Enter your question - ")
                                    llmq(stub, token, "c", question, file_name)
                                elif ans=='2':
                                    listReq(stub, token, "s/as", "")
                                    file_name = input("enter the file to ask about - ")
                                    question = input("Enter your question - ")
                                    llmq(stub, token, "a", question, file_name)
                                elif ans=='3':
                                    question = input("Enter your question - ")
                                    llmq(stub ,token, "g", question, "")
                                # llmq(stub ,token ,"c" , "define cars ?" , "" )
                                elif ans == "4":
                                    question = input("Enter your question - ")
                                    llmq(stub ,token, "gr", question, "")

                            else:
                                print("Invalid option. Please try again.")
                        
                        #instructor
                        elif role == "2":
                            print("\nInstructor Menu:")
                            print("1) Upload Assignments")
                            print("2) Upload Course Material")
                            print("3) download student assignments submissions")
                            print("4) do grading")
                            print("5) list all students ")
                            print("6) Logout")
                            option = input("Enter option: ")

                            if option == '1':
                                files = [f for f in os.listdir("instructor_upload") if os.path.isfile(os.path.join("instructor_upload",f))]
                                for i in files:
                                    print(i + "\n")
                                file_name = input("enter the file to upload - ")
                                upload_file(stub, file_name, "instructor_upload/" , token ,"i/ua")

                            elif option == '2':
                                files = [f for f in os.listdir("instructor_upload") if os.path.isfile(os.path.join("instructor_upload",f))]
                                for i in files:
                                    print(i + "\n")
                                file_name = input("enter the file to upload - ")
                                upload_file(stub, file_name, "instructor_upload/" , token ,"i/cm")

                            elif option == '3':
                                #download assigmnets
                                listReq(stub, token, "i/ss", "")
                                file_name = input("enter the file to Download - ")
                                download_file(stub, file_name, "instructor_download/", token, "i/ss")

                            elif option == '6':
                                token = logout(stub, token)
                                break
                            elif option == '4':
                                grading(stub ,token )
                            elif option == '5':
                                lststudent(stub , token)
                            
                            
                            else:
                                print("Invalid option. Please try again.")
                    
                else: 
                    print(response.message)
            
        elif choice == '2':
            break

if __name__ == '_main_':
    pass
run()