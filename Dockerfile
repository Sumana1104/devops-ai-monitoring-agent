# Use official Node image
FROM node:18

# Set working directory inside container
WORKDIR /app

# Copy package files first (better caching)
COPY package*.json ./

# Install dependencies
RUN npm install

# Copy the rest of the project
COPY . .

COPY .env ./


# Expose the port your app runs on
EXPOSE 3000

# Start the app
CMD ["node", "index.js"]
