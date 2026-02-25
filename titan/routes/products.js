const express = require('express');
const router = express.Router();
const Product = require('../models/Product');



// GET all products
router.get('/', async (req, res) => {
  try {
    const products = await Product.find();
    res.json(products);
  } catch (error) {
    res.status(500).json({ message: error.message });
  }
});

// POST create a product
router.post('/', async (req, res) => {
  try {
    const product = new Product({
      name: req.body.name,
      price: req.body.price,
      description: req.body.description
    });

    const savedProduct = await product.save();
    res.status(201).json(savedProduct);
  } catch (error) {
    res.status(400).json({ message: error.message });
  }
});

module.exports = router;
