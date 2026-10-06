import axios from 'axios';

const getBaseUrl = () => {
  if (typeof window !== 'undefined') {
    const host = window.location.hostname;
    if (host.includes('railway.app') || host.includes('up.railway.app')) {
      return 'https://teekmeet-python-production-py.up.railway.app';
    }
  }
  if (import.meta.env.VITE_API_BASE_URL) {
    return import.meta.env.VITE_API_BASE_URL;
  }
  return 'http://localhost:8000';
};

const API_BASE_URL = getBaseUrl();

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
