
import { WEBHOOK_CONFIG } from '../config';

const TOKEN_KEY = 'token';
const USER_KEY = 'user';

async function handleResponse(response: Response) {
  // Always try to parse JSON to get rich error messages from the backend
  let data;
  try {
    data = await response.json();
  } catch (e) {
    data = null;
  }

  // Handle Unauthorized — only 401 means the session/token is invalid
  if (response.status === 401) {
    const isLoginEndpoint = response.url.includes('login');
    if (!isLoginEndpoint) {
      console.warn('Session expired. Clearing storage.');
      localStorage.removeItem(TOKEN_KEY);
      localStorage.removeItem(USER_KEY);
      localStorage.removeItem('cv_analyzer_auth');
      window.location.reload();
      return new Promise(() => {});
    }
  }
  // 403 Forbidden = valid session but insufficient permissions; let it fall through to error handling

  if (!response.ok) {
    // Throw an error with the backend's specific message if available
    const detail = Array.isArray(data?.detail) ? data.detail.map((d: any) => d.msg).join(', ') : data?.detail;
    const errorMsg = detail || data?.message || data?.error || `API Error: ${response.status} ${response.statusText}`;
    throw new Error(errorMsg);
  }

  return data;
}

export const apiService = {
  async post(url: string, data: any, token?: string) {
    const headers: HeadersInit = {
      'Content-Type': 'application/json',
    };
    
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

  async put(url: string, data: any, token?: string) {
    const headers: HeadersInit = {
      'Content-Type': 'application/json',
    };
    
    const activeToken = token || localStorage.getItem(TOKEN_KEY);
    if (activeToken) {
      headers['Authorization'] = `Bearer ${activeToken}`;
    }

    const response = await fetch(url, {
      method: 'PUT',
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
  },

  async patch(url: string, data: any, token?: string) {
    const headers: HeadersInit = { 'Content-Type': 'application/json' };
    const activeToken = token || localStorage.getItem(TOKEN_KEY);
    if (activeToken) headers['Authorization'] = `Bearer ${activeToken}`;
    const response = await fetch(url, { method: 'PATCH', headers, body: JSON.stringify(data) });
    return handleResponse(response);
  },

  async postForm(url: string, data: FormData, token?: string) {
    const headers: HeadersInit = {};
    const activeToken = token || localStorage.getItem(TOKEN_KEY);
    if (activeToken) {
      headers['Authorization'] = `Bearer ${activeToken}`;
    }
    const response = await fetch(url, { method: 'POST', headers, body: data });
    return handleResponse(response);
  },

  async delete(url: string, token?: string) {
    const headers: HeadersInit = { 'Content-Type': 'application/json' };
    const activeToken = token || localStorage.getItem(TOKEN_KEY);
    if (activeToken) headers['Authorization'] = `Bearer ${activeToken}`;
    const response = await fetch(url, { method: 'DELETE', headers });
    return handleResponse(response);
  },

  async requestPasswordReset(email: string) {
    return this.post(WEBHOOK_CONFIG.FORGOT_PASSWORD_WEBHOOK_URL, { email });
  }
};
