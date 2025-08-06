# Use the official Python image as a base
FROM python:3.11-slim

# Set the working directory in the container
WORKDIR /app

# Copy the requirements file and install dependencies
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of the application code
COPY . .

# Expose the port that Waitress is running on
EXPOSE 8080

# Define the command to run your application
CMD ["waitress-serve", "--host=0.0.0.0", "--port=8080", "app:app"]