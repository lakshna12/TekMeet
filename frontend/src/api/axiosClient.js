import axios from 'axios';

const API_BASE_URL = import.meta.env.VITE_API_BASE_URL || 'http://localhost:8000';

export const axiosClient = axios.create({
  baseURL: API_BASE_URL,
  headers: {
    'Content-Type': 'application/json',
    'Accept': 'application/json',
  },
  timeout: 30000,
});

axiosClient.interceptors.response.use(
  (response) => response,
  (error) => {
    console.error('API Call Error:', error.response || error.message);
    return Promise.reject(error);
  }
);
