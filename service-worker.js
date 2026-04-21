// ✅ CATURA AI SERVICE WORKER - WITH CACHE BUSTING
const CACHE_VERSION = '26.4.13';
const CACHE_NAME = `catura-ai-v${CACHE_VERSION}`;

const FILES_TO_CACHE = [
    '/',
    '/index.html?v=3.0.0',
    '/static/logic.js?v=3.0.0',
    '/static/style.css?v=3.0.0',
    '/static/logo.png',
    '/manifest.json'
];

// ✅ INSTALL EVENT - Cache files on first visit
self.addEventListener('install', (event) => {
    console.log(`✅ Service Worker installing v${CACHE_VERSION}`);
    
    event.waitUntil(
        caches.open(CACHE_NAME)
            .then((cache) => {
                console.log(`✅ Caching files for v${CACHE_VERSION}`);
                return cache.addAll(FILES_TO_CACHE).catch(err => {
                    console.log('Some files could not be cached:', err);
                });
            })
            .then(() => {
                return caches.keys();
            })
            .then((cacheNames) => {
                return Promise.all(
                    cacheNames
                        .filter((cacheName) => cacheName !== CACHE_NAME)
                        .map((cacheName) => {
                            console.log(`🗑️ Deleting old cache: ${cacheName}`);
                            return caches.delete(cacheName);
                        })
                );
            })
    );
    
    self.skipWaiting();
});

// ✅ ACTIVATE EVENT - Clean up and take control
self.addEventListener('activate', (event) => {
    console.log(`✅ Service Worker activating v${CACHE_VERSION}`);
    
    event.waitUntil(
        caches.keys().then((cacheNames) => {
            return Promise.all(
                cacheNames
                    .filter((cacheName) => cacheName !== CACHE_NAME)
                    .map((cacheName) => {
                        console.log(`🗑️ Cleaning up old cache: ${cacheName}`);
                        return caches.delete(cacheName);
                    })
            );
        })
    );
    
    self.clients.claim();
});

// ✅ FETCH EVENT - Network first for HTML, cache first for assets
self.addEventListener('fetch', (event) => {
    const url = new URL(event.request.url);
    
    // Don't cache POST requests or API calls
    if (event.request.method !== 'GET') {
        return;
    }

    // HTML files: Always try network first
    if (event.request.url.includes('.html') || url.pathname === '/') {
        event.respondWith(
            fetch(event.request)
                .then((response) => {
                    if (!response || response.status !== 200) {
                        return caches.match(event.request);
                    }
                    const responseToCache = response.clone();
                    caches.open(CACHE_NAME).then((cache) => {
                        cache.put(event.request, responseToCache);
                    });
                    return response;
                })
                .catch(() => {
                    return caches.match(event.request);
                })
        );
        return;
    }

    // Assets (CSS, JS, images): Cache first, fallback to network
    event.respondWith(
        caches.match(event.request)
            .then((response) => {
                if (response) {
                    fetch(event.request).then((newResponse) => {
                        if (newResponse && newResponse.status === 200) {
                            caches.open(CACHE_NAME).then((cache) => {
                                cache.put(event.request, newResponse);
                            });
                        }
                    }).catch(() => {});
                    return response;
                }
                return fetch(event.request)
                    .then((response) => {
                        if (!response || response.status !== 200) {
                            return response;
                        }
                        const responseToCache = response.clone();
                        caches.open(CACHE_NAME).then((cache) => {
                            cache.put(event.request, responseToCache);
                        });
                        return response;
                    });
            })
            .catch(() => {
                return caches.match(event.request);
            })
    );
});