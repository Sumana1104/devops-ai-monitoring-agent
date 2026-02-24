const express = require('express');
require('dotenv').config();
const app = express();

// -------------------- HEALTH CHECK ROUTE --------------------
app.get("/", (req, res) => {
  res.status(200).send("OK");
});

// -------------------- MIDDLEWARE --------------------
app.use(express.json());

// -------------------- DATABASE CONNECTION --------------------
const connectDB = require('./config/db');
connectDB();

// -------------------- ROUTES --------------------
const productRoutes = require('./routes/products');
app.use('/products', productRoutes);

// -------------------- POST ROUTES --------------------
app.post('/orders', (req, res) => {
  const { userId, items, total } = req.body;

  res.json({
    message: "Order