# Use a lightweight Python 3.11 image as the base
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Copy and install core dependencies from the root requirements.txt
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project directory into the container
COPY . .

# Copy and install dependencies from the 'functions' directory
# The path must be relative to the WORKDIR
COPY functions/requirements.txt ./functions/
RUN pip install --no-cache-dir -r ./functions/requirements.txt

# Use Gunicorn to run the app, binding to the port provided by Cloud Run
CMD gunicorn --bind "0.0.0.0:${PORT}" app:app