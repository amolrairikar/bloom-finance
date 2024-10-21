# Use an official Python runtime as a parent image
FROM python:3.12

# Set the working directory in the container
WORKDIR /backend

# Copy the requirements file into the container
COPY requirements.txt requirements.txt

# Install dependencies
RUN pip install --no-cache-dir -r requirements.txt

# Copy the rest of your application code
COPY . .

# Expose the FastAPI default port (8000)
EXPOSE 8000

# Command to run the FastAPI app using Uvicorn server
CMD ["uvicorn", "main:app", "--host", "0.0.0.0", "--port", "8000"]