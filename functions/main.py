from app import app
from flask import Flask, jsonify, request
import functions_framework

# The Flask application 'app' is imported from your app.py file.
# It is now ready to be served by the functions-framework.

# This function acts as the entry point for your Cloud Function.
@functions_framework.http
def nalam_backend_api(request):
    """
    HTTP Cloud Function entry point that proxies requests to the Flask app.
    """
    # The functions-framework expects a standard WSGI application.
    # The Flask app instance 'app' is already a WSGI callable.
    return app(request.environ, lambda *args, **kwargs: None)