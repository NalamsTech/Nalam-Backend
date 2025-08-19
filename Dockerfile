# Use a lightweight Python 3.11 image as the base
FROM python:3.11-slim

# Set the working directory inside the container
WORKDIR /app

# Copy and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the entire project directory into the container
COPY . .

# Use Gunicorn to run the app, binding to the port provided by Cloud Run
# Use Gunicorn to run the app, with a 60-second timeout
# CMD gunicorn --bind "0.0.0.0:${PORT}" --timeout 60 app:app
CMD ["gunicorn", "--bind", "0.0.0.0:8080", "--timeout", "60", "app:app"]
# CMD ls -la && echo "--- File list from container log ---" && sleep 120