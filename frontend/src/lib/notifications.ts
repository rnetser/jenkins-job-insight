import { api } from './api';

export async function getVapidPublicKey(): Promise<string | null> {
  try {
    const res = await api.get<{ vapid_public_key: string }>('/api/notifications/vapid-public-key');
    return res.vapid_public_key;
  } catch {
    return null;
  }
}

export async function subscribeToPush(): Promise<boolean> {
  try {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return false;

    const vapidKey = await getVapidPublicKey();
    if (!vapidKey) return false;

    const permission = await Notification.requestPermission();
    if (permission !== 'granted') return false;

    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.subscribe({
      userVisibleOnly: true,
      applicationServerKey: urlBase64ToUint8Array(vapidKey),
    });

    const sub = subscription.toJSON();
    await api.post('/api/notifications/subscribe', {
      endpoint: sub.endpoint,
      p256dh_key: sub.keys?.p256dh,
      auth_key: sub.keys?.auth,
    });
    return true;
  } catch {
    return false;
  }
}

export async function unsubscribeFromPush(): Promise<boolean> {
  try {
    const registration = await navigator.serviceWorker.ready;
    const subscription = await registration.pushManager.getSubscription();
    if (!subscription) return true;

    await api.post('/api/notifications/unsubscribe', {
      endpoint: subscription.endpoint,
    });
    await subscription.unsubscribe();
    return true;
  } catch {
    return false;
  }
}

export async function getPushSubscriptionState(): Promise<'granted' | 'denied' | 'default' | 'unsupported'> {
  if (!('serviceWorker' in navigator) || !('PushManager' in window)) return 'unsupported';
  return Notification.permission as 'granted' | 'denied' | 'default';
}

function urlBase64ToUint8Array(base64String: string): Uint8Array {
  const padding = '='.repeat((4 - (base64String.length % 4)) % 4);
  const base64 = (base64String + padding).replace(/-/g, '+').replace(/_/g, '/');
  const rawData = window.atob(base64);
  const outputArray = new Uint8Array(rawData.length);
  for (let i = 0; i < rawData.length; ++i) {
    outputArray[i] = rawData.charCodeAt(i);
  }
  return outputArray;
}
