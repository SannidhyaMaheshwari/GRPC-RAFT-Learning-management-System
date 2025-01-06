import os
import sys
import grpc
from concurrent import futures
import time
import threading
import lms_pb2
import lms_pb2_grpc
import json
from transformers import AutoModelForSeq2SeqLM, AutoTokenizer
import random # Added argparse for command-line arguments

# Load model and tokenizer
model = AutoModelForSeq2SeqLM.from_pretrained("google/flan-t5-base")
tokenizer = AutoTokenizer.from_pretrained("google/flan-t5-base")

# Directory paths
COURSE_MATERIAL_DIR = "resource_course_material"

# In-memory database to store user sessions
users_db_i = {}
users_db_s = {}
tokens = {}
grades = {"student1": []}

# Locks for thread-safe operations
session_lock = threading.Lock()

TOKEN_EXPIRY_TIME = 10 * 60  # 10 minutes in seconds

def invoke_term_change (term):
    global myLeaderId, votedId, timer, myState, myTerm

    if myState != "Follower" or term != myTerm:
        print(f"I am a follower. Term: {term}")

    timer = 0
    myTerm = term
    myLeaderId = -1
    votedId = -1
    myState = "Follower"

def request_vote(term, candidateId, lastLogIndex, lastLogTerm):
    global votedId, myTerm, myState, logs

    if term > myTerm:
        myTerm = term
        votedId = -1

        return request_vote(term, candidateId, lastLogIndex, lastLogTerm)

    # If term < term number on this server, then reply False or if this server already voted on this term, then reply False.
    if term < myTerm or votedId != -1:
        return (myTerm, False)

    #If lastLogIndex < last log index of the server, then reply False
    if lastLogIndex < len(logs):
        return (myTerm, False)

    #If there is lastLogIndex entry on this server, and its term is not equal to lastLogTerm, then reply False.
    if lastLogIndex != 0 and logs[lastLogIndex - 1]["term"] != lastLogTerm:
        return (myTerm, False)

    votedId = candidateId
    myState = "Follower"

    print(f"Voted for node {candidateId}")
    print(f"I am a follower. Term: {myTerm}")

    return (myTerm, True)

# Receives a heartbeat from the leader
def append_entries(term, leaderId, prevLogIndex, prevLogTerm, entries, leaderCommit):
    global myTerm, myState, timer, myLeaderId, logs, commitIndex


    timer = 0

    if myTerm > term:
        return (myTerm, False)

    if prevLogIndex > len(logs):
        return (myTerm, False)

    entriesIndex = 0
    logsIndex = prevLogIndex

    while logsIndex < len(logs) and entriesIndex < len(entries):
        if logs[logsIndex] != entries[entriesIndex]:
            logs = logs[0:logsIndex]
            break

        logsIndex += 1
        entriesIndex += 1

    logs += entries[entriesIndex:]

    if leaderCommit > commitIndex:
        commitIndex = min(leaderCommit, prevLogIndex + len(entries))
    
    if term > myTerm:
        invoke_term_change(term)
    myLeaderId = leaderId

    return (myTerm, True)

# Responds to a GetLeader request from the client
def get_leader():
    global myLeaderId, votedId, servers

    print("Command from client: getleader")

    if myLeaderId != -1:
        print(f"{myLeaderId} {servers[myLeaderId]}")
        return (myLeaderId, servers[myLeaderId])

    if votedId != -1:
        print(f"{votedId} {servers[votedId]}")
        return (votedId, servers[votedId])

    print("None")
    return ("None", "None")

# Executes a Suspend command issued by the client
def suspend(period):
    global suspended, globalPeriod

    print(f"Command from client: suspend {period}")
    print(f'Sleeping for {period} seconds')

    suspended = True
    globalPeriod = period

def replicate_file_to_followers(filename, file_path, file_type):
    print("got for replication")
    global myLeaderId, id

    try:
        # Read config to get the list of followers
        with open("config.conf", "r") as config_file:
            server_list = config_file.readlines()

        for server in server_list:
            server_info = server.split()
            server_id = int(server_info[0])

            # Skip the leader itself
            if server_id == id:
                continue

            server_ip = server_info[1]
            server_port = server_info[2]

            # Create gRPC channel to the follower server
            follower_address = f"{server_ip}:{server_port}"
            channel = grpc.insecure_channel(follower_address)
            stub = lms_pb2_grpc.LMSServiceStub(channel)
            print("created channel for replication")

            # Open the file and stream it to the follower
            with open(file_path, "rb") as file:
                def file_chunks():
                    while True:
                        chunk = file.read(1024 * 1024)  # 1MB chunk size
                        if not chunk:
                            break
                        yield lms_pb2.FileChunk(
                            filename=filename, content=chunk, type=file_type, token="REPLICATION"
                        )
                
                response = stub.UploadFile(file_chunks())
                print("sent for upload")
                if not response.success:
                        print(f"Failed to replicate file {filename} to {follower_address}: {response.message}")
                else:
                        print(f"Successfully replicated {filename} to {follower_address}")

    except Exception as e:
        print(f"Error during file replication: {e}")

def replicate_download_to_followers(filename, file_path, file_type):
    global myLeaderId

    try:
        # Read config to get the list of followers
        with open("config.conf", "r") as config_file:
            server_list = config_file.readlines()

        for server in server_list:
            server_info = server.split()
            server_id = int(server_info[0])

            # Skip the leader itself
            if server_id == myLeaderId:
                continue

            server_ip = server_info[1]
            server_port = server_info[2]

            # Create gRPC channel to the follower server
            follower_address = f"{server_ip}:{server_port}"
            channel = grpc.insecure_channel(follower_address)
            stub = lms_pb2_grpc.LMSServiceStub(channel)

            with open(file_path, "rb") as file:
                def file_chunks():
                    while True:
                        chunk = file.read(1024 * 1024)  # 1MB chunk size
                        if not chunk:
                            break
                        yield lms_pb2.FileChunk(
                            filename=filename, content=chunk, type=file_type, token="REPLICATION"
                        )
                
                response = stub.ReplicateFile(file_chunks())
                if not response.success:
                    print(f"Failed to replicate file {filename} to {follower_address}: {response.message}")



    except Exception as e:
        print(f"Error during file replication: {e}")


# LMS gRPC Server class
class LMSServer(lms_pb2_grpc.LMSServiceServicer):


    def RequestVote(self, request, context):
        term = request.candidate.term
        candidate_id = request.candidate.candidateID
        last_log_index = request.lastLogIndex
        last_log_term = request.lastLogTerm

        voted = request_vote(term, candidate_id, last_log_index, last_log_term)

        result = lms_pb2.TermResultPair(term=voted[0], verdict=voted[1])

        response = lms_pb2.RequestVoteResponse(result=result)

        return response
    
    def AppendEntries(self, request, context):
        term = request.leader.term
        leader_id = request.leader.leaderID
        prev_log_index = request.prevLogIndex
        prev_log_term = request.prevLogTerm
        
        entries = []

        for entry in request.entries:
            entries.append(extract_log_entry_message(entry))

        leader_commit = request.leaderCommit

        appended = append_entries(term, leader_id, prev_log_index, prev_log_term, entries, leader_commit)

        result = lms_pb2.TermResultPair(term=appended[0], verdict=appended[1])

        response = lms_pb2.AppendEntriesResponse(result=result)

        return response

    def GetLeader(self, request, context):
        leader = get_leader()

        if leader[0] == "None":
            response = lms_pb2.GetLeaderResponse(nodeId=-1)
        
        else:
            response = lms_pb2.GetLeaderResponse(nodeId=leader[0], nodeAddress=leader[1])
        
        return response
    
    def Suspend(self, request, context):
        period = request.period

        suspend(period)

        return lms_pb2.Empty()
    
    def ReplicateFile(self, request_iterator, context):
        try:
            filename = None
            file_path = None
            type=""
            global myState

            for file_chunk in request_iterator:
                if filename is None:
                    filename = file_chunk.filename

                    type = file_chunk.type
                    path = "student_download/" if "s" in type else "instructor_download/"

                    if not os.path.exists(path):
                        os.makedirs(path)    
                    file_path = os.path.join(path, filename)

                with open(file_path, "ab") as f:
                    f.write(file_chunk.content)

                

            return lms_pb2.UploadStatus(success=True, message="File uploaded successfully")

        except Exception as e:
            return lms_pb2.UploadStatus(success=False, message=str(e))

    

    def UploadFile(self, request_iterator, context):
        try:
            filename = None
            file_path = None
            type=""
            is_replication = False
            global myState

            for file_chunk in request_iterator:
                if filename is None:
                    filename = file_chunk.filename

                    if file_chunk.token == "REPLICATION":
                        is_replication= True
                        pass
                    #check token 
                    elif file_chunk.token not in tokens:
                        return lms_pb2.UploadStatus(success=False, message="you are not logged in OR login expired")
                    #type decide loaction to save file
                    type = file_chunk.type
                    if file_chunk.type == "s/ua":
                        path = "Students_Solutions/"
                        with open('grade.json', 'r') as openfile:
                            d = json.load(openfile)
        
                        d[filename] = ""
                        with open("grade.json", "w") as outfile:
                            json.dump(d, outfile)

                    elif file_chunk.type == "i/ua":
                        path= "Instructor/Assignments"

                    elif file_chunk.type == "i/cm":
                        path = "Instructor/Course_material"
                    else:
                        path = file_chunk.path

                    if not os.path.exists(path):
                        os.makedirs(path)    
                    file_path = os.path.join(path, filename)
                    #path store where but  add contion for /to exist

                # Write the content of the file to the specified path
                with open(file_path, "ab") as f:
                    f.write(file_chunk.content)
            
            
            if not is_replication:
                print("sent for replication")
                replicate_file_to_followers(filename, file_path, type)

            return lms_pb2.UploadStatus(success=True, message="File uploaded successfully")
        except Exception as e:
            return lms_pb2.UploadStatus(success=False, message=str(e))
        
    def listReq(self , request ,context):
        path = ""
        if request.token not in tokens:
            return lms_pb2.listResponse(type = "login" , lst = "you are not logged in OR login expired")
        
        # if request.type == "s/g":
        #     with open('grade.json', 'r') as openfile:
    
        #         d = json.load(openfile)  # This loads the JSON content as a Python dictionary
        #         #token = request.token
        #         #student = tokens[token][0]
        #         #d_s = {x:y for (x,y) in d if student in x}

        #         json_string = json.dumps(d)
        #         return lms_pb2.listResponse(type = "ok",lst=json_string)
        if request.type == "s/q":
            with open('queries/q.json', 'r') as openfile:
    
                json_object = json.load(openfile)  # This loads the JSON content as a Python dictionary

                json_string = json.dumps(json_object)
                return lms_pb2.listResponse(type = "ok",lst=json_string)
        if request.type == "i/s":
            with open('userDb_s.json', 'r') as openfile:
    
                json_object = json.load(openfile)  # This loads the JSON content as a Python dictionary

                json_string = json.dumps(json_object)
                return lms_pb2.listResponse(type = "ok",lst=json_string)            
        if request.type == "i/g" or request.type == 's/g':
            with open('grade.json', 'r') as openfile:
    
                json_object = json.load(openfile)  # This loads the JSON content as a Python dictionary

                json_string = json.dumps(json_object)
                return lms_pb2.listResponse(type = "ok",lst=json_string)
        
        if request.type == "s/as":
            path = 'Instructor/Assignments'
        elif request.type == "s/cm":
            path = 'Instructor/Course_material'
        elif request.type =="i/ss":
            path = "Students_Solutions"
        else:
            path = request.path
            
        files = [f for f in os.listdir(path) if os.path.isfile(os.path.join(path, f))]
        lst = "##".join(files)
        #lst+= request.type
        return lms_pb2.listResponse(type = "ok",lst=lst)

    def DownloadFile(self, request, context):
        if request.token not in tokens:
            context.set_details(f'you are not logged in OR login expired')
        path = ""
        if request.type == "s/as":
            path="Instructor/Assignments"
        elif request.type == "s/cm":
            path="Instructor/Course_Material"
        elif request.type =="i/ss":
            path = "Students_Solutions"
        else:
            path=request.path
        #from where to download
        
        file_path = os.path.join(path,request.filename)
        #path in join from where

        # download_path = os.path.join("student_download" if "s" in request.type else "instructor_download", request.filename)
        
        # Save the file locally on the server (simulate the client-side behavior)

        print(f"File successfully downloaded and saved to {file_path} on the server.")

        if myState == 'Leader':
            replicate_download_to_followers(request.filename, file_path, request.type)


        try:                
            with open(file_path, "rb") as f:
                while chunk := f.read(4096):
                    yield lms_pb2.FileChunk(content=chunk, filename=request.filename)

        except FileNotFoundError:
            context.set_details(f'File {request.filename} not found')
            context.set_code(grpc.StatusCode.NOT_FOUND)

   

    def login(self, request, context):
        current_time = int(time.time())  # Capture the current time in seconds
        if request.role == '1':
            if request.username in users_db_s and users_db_s[request.username] == request.password:
                token = f"session_{request.username}_{current_time}"
                with session_lock:
                    tokens[token] = (request.username, current_time)  # Store username and creation time
                return lms_pb2.LoginResponse(status=True, token=token, message="Login successful: " + token)
        elif request.role == '2':
            if request.username in users_db_i and users_db_i[request.username] == request.password:
                token = f"session_{request.username}_{current_time}"
                with session_lock:
                    tokens[token] = (request.username, current_time)  # Store username and creation time
                return lms_pb2.LoginResponse(status=True, token=token, message="Login successful: " + token)
        return lms_pb2.LoginResponse(status=False, message="Invalid username or password or role chosen is wrong")

    def logout(self, request, context):
        with session_lock:
            if request.token in tokens:
                del tokens[request.token]
                return lms_pb2.LogoutResponse(status=True, message="Logout successful")
        return lms_pb2.LogoutResponse(status=False, message="Invalid session token")
    def grade(self, request, context):
        with open('grade.json', 'r') as openfile:
            d = json.load(openfile)
        
        d[request.filename] = request.no
        with open("grade.json", "w") as outfile:
            json.dump(d, outfile)
        return lms_pb2.gradeRes(status=True, message="done")
    
    def llmq(self, request, context):
        if request.token not in tokens:
            return lms_pb2.listResponse(type = "login" , lst = "you are not logged in OR login expired")
        
        que = request.quetion
        que += """
              from the given below context ans above quetion
               """
        if request.type == "c":
            fn = request.filename
            f = open("Instructor/Course_material/"+fn, "r")
            cntext = f.read()
            que += cntext
        elif request.type == "a":
            fn = request.filename
            f = open("Instructor/Assignments/"+fn, "r")
            cntext = f.read()
            que += cntext
        elif request.type == "g":
            cntext = ""
            que += cntext
        elif request.type == "gr":
            que += f""" given below is a json file in which we have key value pair of filename : grades give me grades of ALL the file have string {tokens[request.token][0]} in filename
                    remember list ALL file have my name 
                    """
            
            #que = self.gfile("grade.json" ,tokens[request.token][0] )
            b=tokens[request.token][0]
            with open('grade.json', 'r') as openfile:
                d = json.load(openfile)
            str = ""
            for x,y in d.items():
                if b in x:
                    str += x + " : " + y + "\n"
            que += str

        else:
            answer = "out of my scope"

        inputs = tokenizer(que, truncation=True, max_length=2000, return_tensors="pt")

        # Generate outputs with more controlled settings for length and quality
        outputs = model.generate(
            **inputs,
            do_sample=True,
            no_repeat_ngram_size=3, 
            max_new_tokens=500,  # Generates a longer response
            num_beams=5,         # Beam search for better output
            temperature=0.9,     # Slight randomness for variety
            )   

        # Decode and print the output
        answer = tokenizer.batch_decode(outputs, skip_special_tokens=True)[0]
        return lms_pb2.llmres(status=True, message="done" , ans = answer)
# Background task to clean up expired tokens
def clean_expired_tokens():
    while True:
        time.sleep(60)  # Check every 60 seconds
        current_time = int(time.time())
        with session_lock:
            expired_tokens = [token for token, (username, timestamp) in tokens.items() if current_time - timestamp > TOKEN_EXPIRY_TIME]
            for token in expired_tokens:
                del tokens[token]
                print(f"Token {token} expired and removed.")


def main_loop():
    global timer, suspended

    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    lms_pb2_grpc.add_LMSServiceServicer_to_server(LMSServer(), server)
    server.add_insecure_port(servers[id])
    server.start()

    cleanup_thread = threading.Thread(target=clean_expired_tokens, daemon=True)
    cleanup_thread.start()

    while True:

        if suspended:
            server.stop(0)
            time.sleep(globalPeriod)
            server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
            lms_pb2_grpc.add_LMSServiceServicer_to_server(LMSServer(), server)
            server.add_insecure_port(servers[id])
            server.start()
            suspended = False

        init_servers()
        handle_current_state()

        time.sleep(0.001)
        timer += 1

def handle_leader_init():
    global myState, myLeaderId, nextIndex, matchIndex

    myState = "Leader"
    print(f"I am a leader. Term: {myTerm}")
    myLeaderId = id
    nextIndex = {}
    matchIndex = {}

    for key in servers:

        if key == id:
            continue
        
        nextIndex[key] = commitIndex + 1
        matchIndex[key] = 0


# Applies the necessary immediate changes upon turning into a candidate
# Sends RequestVote requests to other nodes, and decides whether or not to turn into the leader
def handle_candidate_init():
    global timer, id, myTerm, myState, votedId, myLeaderId

    print("The leader is dead")

    timer = 0
    
    invoke_term_change(myTerm + 1)
    
    myState = "Candidate"
    
    print(f"I am a candidate. Term: {myTerm}")
    
    vote_count = 1
    votedId = id
    
    print(f"Voted for node {id}")
    
    for key in servers:

        if key == id:
            continue
        try:
            address = servers[key]

            channel = grpc.insecure_channel(address)
            stub = lms_pb2_grpc.LMSServiceStub(channel)
            candidate = lms_pb2.TermCandIDPair(term=myTerm, candidateID=id)
            lastLogTerm = 0
            if len(logs) != 0:
                lastLogTerm = logs[-1]["term"]
            params = lms_pb2.RequestVoteRequest(candidate=candidate, lastLogIndex=len(logs), lastLogTerm=lastLogTerm)
            response = stub.RequestVote(params)

            term, result = response.result.term, response.result.verdict

        except grpc.RpcError:
            continue
        
        if term > myTerm:

            invoke_term_change(term)

            return

        vote_count += (result == True)

    print("Votes received")

    if (vote_count >= len(servers)/2 + 1):
        handle_leader_init()


def handle_follower():
    global myState, timer, timerLimit

    if timer >= timerLimit:
        handle_candidate_init()

# Handles the candidate state
def handle_candidate():
    global timer, timerLimit, myState, logs

    if timer >= timerLimit:
        timerLimit = random.randint(1000,1500)
        invoke_term_change(myTerm)

# Extracts a log entry from a ProtoBuf LogEntry message
def extract_log_entry_message(entry):
    return {'term' : entry.term, 'command' : entry.command}

# Forms a log entry into a ProtoBuf LogEntry message
def form_log_entry_message(entry):
    return lms_pb2.LogEntry(term=entry['term'], command=entry['command'])



def apply_commits():
    global myDict, lastApplied

    while lastApplied < commitIndex:
        command = logs[lastApplied]['command'].split(' ')
        myDict[command[0]] = command[1]

        lastApplied += 1

def handle_leader():
    global timer, myTerm, id, commitIndex, nextIndex, matchIndex, logs

    if timer % 50 == 0:
        
        # Resets the timer to avoid overflowing in an infinite leader state
        # Applicable since the leader does not care about the timer limit
        timer = 0

        for key in servers:
            
            if key == id:
                continue

            while True:
                try:
                    # Sending an AppendEntries request

                    address = servers[key]
                    channel = grpc.insecure_channel(address)
                    stub = lms_pb2_grpc.LMSServiceStub(channel)
                    prevLogIndex = nextIndex[key] - 1
                    prevLogTerm = 0
                    if len(logs) != 0 and prevLogIndex != 0:
                        prevLogTerm = logs[prevLogIndex - 1]['term']
                    
                    paramEntries = [form_log_entry_message(x) for x in logs[prevLogIndex:]]
                    leader = lms_pb2.TermLeaderIDPair(term=myTerm, leaderID=id)
                    params = lms_pb2.AppendEntriesRequest(leader=leader, prevLogIndex=prevLogIndex, prevLogTerm=prevLogTerm, leaderCommit=commitIndex)
                    params.entries.extend(paramEntries)
                    response = stub.AppendEntries(params)

                    term, result = response.result.term, response.result.verdict
                        
                except grpc.RpcError:
                    break
                
                # If successful: update nextIndex and matchIndex for the follower.
                if result == True:
                    nextIndex[key] = matchIndex[key] = prevLogIndex + len(paramEntries) + 1
                    matchIndex[key] -= 1
                    nextIndex[key] = max(nextIndex[key], 1)
                    break
                
                # If, as a result of calling this function, the Leader receives a term number greater than its own term number
                # that Leader must update his term number and become a Follower
                if term > myTerm:
                    invoke_term_change(term)
                    return

                # If AppendEntries fails because of log inconsistency: decrement nextIndex and retry.
                nextIndex[key] -= 1
                if nextIndex[key] == 0:
                    nextIndex[key] = 1
                    break
    
        while commitIndex < len(logs):
            newCommitIndex = commitIndex + 1
            validServers = 1
            
            for key in matchIndex:
                if matchIndex[key] >= newCommitIndex:
                    validServers += 1

            if validServers >= len(servers)/2 + 1:
                commitIndex = newCommitIndex
            else:
                break


def handle_current_state():
    apply_commits()
    if myState == "Follower":
        handle_follower()
    if myState == "Candidate":
        handle_candidate()
    elif myState == "Leader":
        handle_leader()

# Start the gRPC server
def serve():

    
    server = grpc.server(futures.ThreadPoolExecutor(max_workers=10))
    lms_pb2_grpc.add_LMSServiceServicer_to_server(LMSServer(), server)
    server.add_insecure_port('[::]:50051')
    server.start()

    # Start the background thread for cleaning expired tokens
    cleanup_thread = threading.Thread(target=clean_expired_tokens, daemon=True)
    cleanup_thread.start()


    print(f"Server started on 50051")
    try:
        while True:
            time.sleep(86400)  # Keep the server running
    except KeyboardInterrupt:
        server.stop(0)

# if _name_ == '_main_':
    
#     # Ensure the directories exist
#     if not os.path.exists('Instructor/Assignments'):
#         os.makedirs('Instructor/Assignments')
#     if not os.path.exists('Instructor/Course_material'):
#         os.makedirs('Instructor/Course_material')
#     if not os.path.exists('Students_Solutions'):
#         os.makedirs('Students_Solutions')

#     # Load user databases
#     with open('userDB_i.json', 'r') as openfile:
#         users_db_i = json.load(openfile)
#     with open('userDB_s.json', 'r') as openfile:
#         users_db_s = json.load(openfile)
#     #print(users_db_s)

#     os.makedirs(COURSE_MATERIAL_DIR, exist_ok=True)
#     serve()


def startup():
    global id, myTerm, timer, timerLimit, myState, votedId, servers, myLeaderId, suspended, globalPeriod, commitIndex, lastApplied, logs, myDict, nextIndex, matchIndex

    init_servers()

    id = int(sys.argv[1])
    suspended = False
    myDict = {}
    logs = []
    nextIndex = {}
    matchIndex = {}
    lastApplied = 0
    commitIndex = 0
    globalPeriod = 0
    myTerm = 0
    timer = 0
    timerLimit = random.randint(1000, 1500)
    myState = "Follower"
    votedId = -1
    myLeaderId = -1
    
    print(f"The server starts at {servers[id]}")
    print("I am a follower. Term: 0")

def init_servers():
    global servers
    servers = {}

    with open("config.conf", "r") as f:
        for line in f:
            servers[int(line.split(" ")[0])] = f'{line.split(" ")[1]}:{line.split(" ")[2].rstrip()}'


def init():
    global users_db_i, users_db_s
    # Ensure the directories exist
    if not os.path.exists('Instructor/Assignments'):
        os.makedirs('Instructor/Assignments')
    if not os.path.exists('Instructor/Course_material'):
        os.makedirs('Instructor/Course_material')
    if not os.path.exists('Students_Solutions'):
        os.makedirs('Students_Solutions')
    if not os.path.exists('student_download'):
        os.makedirs('student_download')
    if not os.path.exists('instructor_download'):
        os.makedirs('instructor_download')

    # Load user databases
    with open('userDB_i.json', 'r') as openfile:
        users_db_i = json.load(openfile)
    with open('userDB_s.json', 'r') as openfile:
        users_db_s = json.load(openfile)
    #print(users_db_s)

    os.makedirs(COURSE_MATERIAL_DIR, exist_ok=True)
    try:
        startup()
        main_loop()
    except KeyboardInterrupt:
        terminate()

def terminate():
    print(f"Server {id} is shutting down...")
    sys.exit(0)

init()