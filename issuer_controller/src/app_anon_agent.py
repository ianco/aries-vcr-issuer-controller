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

            # post a confirmation web hook
            state = "stored"
            thread_id = str(uuid.uuid4())
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
