const express = require('express');
require('dotenv').config();

const app = express();

// -------------------- MIDDLEWARE --------------------
app.use(express.json());

// -------------------- HEALTH CHECK ROUTE --------------------
// This is required for ALB health checks
app.get("/", (req, res) => { res.send("OK"); });

// -------------------- DATABASE CONNECTION --------------------
const connectDB = require('./config/db');

connectDB()
  .then(() => console.log("Database connected"))
  .catch((err) => {
    console.error("Database connection failed:", err);
    process.exit(1); // Stop container if DB fails
  });

// -------------------- ROUTES --------------------
const productRoutes = require('./routes/products');
app.use('/products', productRoutes);

// -------------------- POST ROUTES --------------------
app.post('/orders', (req, res) => {
  const { userId, items, total } = req.body;

  if (!userId || !items || !total) {
    return res.status(400).json({
      message: "Missing required fields"
    });
  }

  res.status(201).json({
    message: "Order created successfully",
    order: {
      userId,
      items,
      total
    }
  });
});

// -------------------- GLOBAL ERROR HANDLER --------------------
app.use((err, req, res, next) => {
  console.error(err.stack);
  res.status(500).json({
    message: "Internal Server Error"
  });
});

// -------------------- SERVER --------------------
const PORT = process.env.PORT || 3000;

app.listen(PORT, "0.0.0.0", () => { console.log(`Server running on port ${PORT}`); });