
import { WEBHOOK_CONFIG } from '../config';

const TOKEN_KEY = 'token';
const USER_KEY = 'user';

async function handleResponse(response: Response) {
  if (response.status === 401 || response.status === 403) {
    console.warn('Session expired or unauthorized. Clearing storage.');
    localStorage.removeItem(TOKEN_KEY);
    localStorage.removeItem(USER_KEY);
    localStorage.removeItem('cv_analyzer_auth');
    
    window.location.reload();
    return new Promise(() => {});
  }

  if (!response.ok) {
    const errorData = await response.json().catch(() => ({}));
    throw new Error(errorData.message || `API Error: ${response.status} ${response.statusText}`);
  }
  return response.json();
}

export const apiService = {
  async post(url: string, data: any, token?: string) {
    const headers: HeadersInit = {
      'Content-Type': 'application/json',
    };
    
    // Read real token from storage, never use {{ }}
    const activeToken = token || localStorage.getItem(TOKEN_KEY);
    if (activeToken) {
      headers['Authorization'] = `Bearer ${activeToken}`;
    }

    const response = await fetch(url, {
      method: 'POST',
      headers,
      body: JSON.stringify(data),
    });
    return handleResponse(response);
  },

  async get(url: string, params: Record<string, string> = {}, token?: string) {
    const headers: HeadersInit = {
      'Content-Type': 'application/json',
    };

    const activeToken = token || localStorage.getItem(TOKEN_KEY);
    if (activeToken) {
      headers['Authorization'] = `Bearer ${activeToken}`;
    }

    const queryString = new URLSearchParams(params).toString();
    const fullUrl = queryString ? `${url}?${queryString}` : url;

    const response = await fetch(fullUrl, {
      method: 'GET',
      headers,
    });
    return handleResponse(response);
  }
};
