const express = require('express');
require('dotenv').config();
const app = express();

const productRoutes = require('./routes/products');
app.use('/products', productRoutes);


// Middleware to parse JSON
app.use(express.json());

// Database connection
const connectDB = require('./config/db');
connectDB();

// -------------------- POST ROUTES --------------------

app.post('/orders', (req, res) => {
  const { userId, items, total } = req.body;

  res.json({
    message: "Order created successfully",
    order: {
      id: 200,
      userId,
      items,
      total,
      status: "processing"
    }
  });
});

app.post('/auth/register', (req, res) => {
  res.json({
    message: "User registered successfully",
    user: {
      id: 3,
      name: "New User",
      email: "newuser@example.com"
    }
  });
});

app.post('/auth/login', (req, res) => {
  res.json({
    message: "Login successful",
    token: "fake-jwt-token-12345"
  });
});

// -------------------- GET ROUTES --------------------

app.get('/', (req, res) => {
  res.send('E-commerce API is running');
});

app.get('/products', (req, res) => {
  res.json([
    { id: 1, name: "Laptop", price: 999 },
    { id: 2, name: "Headphones", price: 199 },
    { id: 3, name: "Keyboard", price: 99 }
  ]);
});

app.get('/users', (req, res) => {
  res.json([
    { id: 1, name: "Suman", email: "suman@example.com" },
    { id: 2, name: "John", email: "john@example.com" }
  ]);
});

app.get('/orders', (req, res) => {
  res.json([
    { id: 101, userId: 1, total: 1298, status: "shipped" },
    { id: 102, userId: 2, total: 199, status: "processing" }
  ]);
});

app.listen(3000, () => {
  console.log('Server running on port 3000');
});
