
import { WEBHOOK_CONFIG } from '../config';

const AUTH_STORAGE_KEY = 'cv_analyzer_auth';

async function handleResponse(response: Response) {
  // Centralized handling for 401/403 status codes
  if (response.status === 401 || response.status === 403) {
    console.warn('Session expired or unauthorized. Clearing session and redirecting.');
    localStorage.removeItem(AUTH_STORAGE_KEY);
    // Reload the page to force the SPA state back to unauthenticated (AuthPage)
    window.location.reload();
    // Return a never-resolving promise to stop execution of the calling code
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
    
    // JWT as Bearer token in Authorization header
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
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

    // JWT as Bearer token in Authorization header
    if (token) {
      headers['Authorization'] = `Bearer ${token}`;
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
