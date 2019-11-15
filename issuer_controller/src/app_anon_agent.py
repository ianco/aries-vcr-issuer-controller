#!/usr/bin/env python
from flask import Flask, jsonify, abort, request, make_response

import requests
import json
import os
import threading
import uuid
import time
import yaml
import config
import pytz
from datetime import datetime

# Load application settings (environment)
config_root = os.environ.get('CONFIG_ROOT', '../config')
ENV = config.load_settings(config_root=config_root)

class Controller(Flask):
    def __init__(self):
        print("Initializing " + __name__ + " ...")
        super().__init__(__name__)

app_anon_agent = Controller()
wsgi_app = app_anon_agent.wsgi_app

@app_anon_agent.route('/health', methods=['GET'])
def health_check():
    return make_response(jsonify({'success': True}), 200)

@app_anon_agent.errorhandler(404)
def not_found(error):
    return make_response(jsonify({'error': 'Not found'}), 404)

DID = None
TOB_CONNECTION_ID = None
SCHEMAS = None
CRED_DEFS = None
agent_lock = threading.Lock()

def get_did():
    global DID
    if not DID:
        try:
            agent_lock.acquire()

            if not DID:
                print("Calling get_did()")
                response = requests.get(
                        "http://myorg-agent:8034/wallet/did/public",
                        headers = {"accept": "application/json"}
                    )
                response.raise_for_status()
                result = response.json()
                did = result["result"]
                print("Fetched DID from agent: ", did)
                DID = did["did"]
        finally:
            agent_lock.release()

    return DID

def load_cred_defs():
    global CRED_DEFS
    if not CRED_DEFS:
        try:
            agent_lock.acquire()

            if not CRED_DEFS:
                print("Calling load_cred_defs()")

                # load schemas
                response = requests.get(
                        "http://myorg-agent:8034/schemas/created",
                        headers = {"accept": "application/json"}
                    )
                response.raise_for_status()
                schs_result = response.json()
                SCHEMAS = {}
                for schema_id in schs_result["schema_ids"]:
                    response = requests.get(
                            "http://myorg-agent:8034/schemas/" + schema_id,
                            headers = {"accept": "application/json"}
                        )
                    response.raise_for_status()
                    sch_result = response.json()
                    SCHEMAS[schema_id] = sch_result["schema_json"]

                # load cred defs
                response = requests.get(
                        "http://myorg-agent:8034/credential-definitions/created",
                        headers = {"accept": "application/json"}
                    )
                response.raise_for_status()
                cds_result = response.json()
                CRED_DEFS = {}
                for cred_def_id in cds_result["credential_definition_ids"]:
                    response = requests.get(
                            "http://myorg-agent:8034/credential-definitions/" + cred_def_id,
                            headers = {"accept": "application/json"}
                        )
                    response.raise_for_status()
                    cd_result = response.json()
                    CRED_DEFS[cred_def_id] = { "cred_def": cd_result["credential_definition"], "schema_id": None }
                    schema_seq = cd_result["credential_definition"]["schemaId"]
                    for schema in SCHEMAS:
                        if str(SCHEMAS[schema]["seqNo"]) == schema_seq:
                            CRED_DEFS[cred_def_id]["schema_id"] = SCHEMAS[schema]["id"]
                            break

        finally:
            agent_lock.release()

def get_schema_id(cred_def_id):
    load_cred_defs()
    cred_def = CRED_DEFS[cred_def_id]
    return cred_def["schema_id"]

class SendCredentialThread(threading.Thread):
    def __init__(self, cred_input, cred_exch_id, url, headers):
        threading.Thread.__init__(self)
        self.cred_input = cred_input
        self.cred_exch_id = cred_exch_id
        self.url = url
        self.headers = headers

    def run(self):
        cred_data = None
        try:
            # delay
            #time.sleep(0.01)

            # check if we have the issuing agent's DID (if not get it)
            agent_did = get_did()
            tob_connection_id = self.cred_input["connection_id"]
            thread_id = str(uuid.uuid4())

            # post the credential directly to ICOB
            tob_url = "http://tob-api:8080/agentcb/topic/credentials/"
            tob_state = "credential_received"
            cred_def_id = self.cred_input["credential_definition_id"]
            schema_id = get_schema_id(cred_def_id)
            updated_date = str(datetime.now(pytz.utc))
            credential_msg = {
                "auto_issue": False,
                "connection_id": tob_connection_id,
                "created_at": updated_date,
                "credential_definition_id": cred_def_id,
                "credential_exchange_id": self.cred_exch_id,
                "credential_offer": {
                    "Not": "Used"
                } ,
                "credential_request": {
                    "Not": "Used"
                } ,
                "credential_request_metadata": {
                    "Not": "Used"
                } ,
                "initiator": "external",
                "raw_credential": {
                    "cred_def_id": cred_def_id,
                    "rev_reg": None,
                    "rev_reg_id": None,
                    "schema_id": schema_id,
                    "signature": {
                        "Not": "Used"
                    } ,
                    "signature_correctness_proof": {
                        "Not": "Used"
                    } ,
                    "values": {
                    },
                    "witness": None
                },
                "schema_id": schema_id,
                "state": tob_state,
                "thread_id": thread_id,
                "updated_at": updated_date
            }
            for key in self.cred_input["credential_values"]:
                key_value = {
                    "raw": self.cred_input["credential_values"][key],
                    "encoded": "Not used"
                }
                credential_msg["raw_credential"]["values"][key] = key_value
            #print(credential_msg)

            # post to ICOB
            response = requests.post(
                tob_url, json.dumps(credential_msg), headers=self.headers
            )
            response.raise_for_status()

            # post a confirmation web hook
            state = "stored"
            response_msg = {
                    "state": state, 
                    "credential_exchange_id": self.cred_exch_id, 
                    "thread_id": thread_id, 
                    "message": self.cred_input
                }
            response = requests.post(
                self.url, json.dumps(response_msg), headers=self.headers
            )
            response.raise_for_status()

        except Exception as exc:
            # don't re-raise; just print status
            print("Failed to call callback", exc)

ADMIN_REQUEST_HEADERS = {"Content-Type": "application/json"}

@app_anon_agent.route('/api/credential_exchange/send', methods=['POST'])
def submit_credential():
    """
    Exposed method to proxy credential issuance requests.
    """
    if not request.json:
        abort(400)

    cred_input = request.json
    cred_exch_id = str(uuid.uuid4())
    connection_id = str(uuid.uuid4())

    # TODO short circuit a credential issue; post directly to ICOB
    # TODO start a thread that will return a confirmation web hook after a timeout
    thread = SendCredentialThread(
        cred_input,
        cred_exch_id,
        "http://myorg-controller:5000/api/agentcb/topic/credentials",
        ADMIN_REQUEST_HEADERS,
    )
    thread.start()

    cred_response = jsonify({"credential_exchange_id": cred_exch_id,
                    "connection_id": connection_id})
    return cred_response

@app_anon_agent.route('/api/agentcb/topic/<topic>/', methods=['POST'])
def agent_callback(topic):
    """
    Main callback for aries agent.  Dispatches calls based on the supplied topic.
    """
    if not request.json:
        abort(400)

    message = request.json

    # dispatch based on the topic type
    if topic == issuer.TOPIC_CONNECTIONS:
        if "state" in message:
            # TODO
            return jsonify({})
        return jsonify({})

    elif topic == issuer.TOPIC_CONNECTIONS_ACTIVITY:
        return jsonify({})

    elif topic == issuer.TOPIC_CREDENTIALS:
        if "state" in message:
            # TODO 
            return jsonify({})
        return jsonify({})

    elif topic == issuer.TOPIC_PRESENTATIONS:
        if "state" in message:
            # TODO 
            return jsonify({})
        return jsonify({})

    elif topic == issuer.TOPIC_GET_ACTIVE_MENU:
        return jsonify({})

    elif topic == issuer.TOPIC_PERFORM_MENU_ACTION:
        return jsonify({})

    elif topic == issuer.TOPIC_ISSUER_REGISTRATION:
        return jsonify({})
    
    elif topic == issuer.TOPIC_PROBLEM_REPORT:
        return jsonify({})

    else:
        print("Callback: topic=", topic, ", message=", message)
        abort(400, {'message': 'Invalid topic: ' + topic})
