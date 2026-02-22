const mongoose = require('mongoose');

const connectDB = async () => {
  try {
    await mongoose.connect('mongodb://host.docker.internal:27017/ecommerce');

    console.log('MongoDB connected');
  } catch (error) {
    console.error('Database connection failed:', error);
  }
};

module.exports = connectDB;
