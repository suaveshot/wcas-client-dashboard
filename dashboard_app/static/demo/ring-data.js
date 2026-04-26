// Vendor SVGs lifted from the activate UI kit (verbatim - they are the
// canonical brand identity for the rings grid).

window.VENDOR_SVG = {
  google: '<svg width="36" height="36" viewBox="0 0 24 24"><path fill="#4285F4" d="M22.56 12.25c0-.78-.07-1.53-.2-2.25H12v4.26h5.92c-.26 1.37-1.04 2.53-2.21 3.31v2.77h3.57c2.08-1.92 3.28-4.74 3.28-8.09z"/><path fill="#34A853" d="M12 23c2.97 0 5.46-.98 7.28-2.66l-3.57-2.77c-.98.66-2.23 1.06-3.71 1.06-2.86 0-5.29-1.93-6.16-4.53H2.18v2.84C3.99 20.53 7.7 23 12 23z"/><path fill="#FBBC05" d="M5.84 14.09c-.22-.66-.35-1.36-.35-2.09s.13-1.43.35-2.09V7.07H2.18C1.43 8.55 1 10.22 1 12s.43 3.45 1.18 4.93l3.66-2.84z"/><path fill="#EA4335" d="M12 5.38c1.62 0 3.06.56 4.21 1.64l3.15-3.15C17.45 2.09 14.97 1 12 1 7.7 1 3.99 3.47 2.18 7.07l3.66 2.84c.87-2.6 3.3-4.53 6.16-4.53z"/></svg>',
  seo: '<svg width="36" height="36" viewBox="0 0 24 24"><circle cx="10" cy="10" r="6.5" fill="none" stroke="#4285F4" stroke-width="2"/><rect x="7.5" y="10.5" width="1.6" height="2.5" fill="#34A853"/><rect x="9.8" y="7.5" width="1.6" height="5.5" fill="#FBBC05"/><rect x="12.1" y="9" width="1.6" height="4" fill="#EA4335"/><line x1="14.6" y1="14.6" x2="20" y2="20" stroke="#4285F4" stroke-width="2.2" stroke-linecap="round"/></svg>',
  reviews: '<svg width="36" height="36" viewBox="0 0 24 24"><path d="M12 2.5 14.6 8.4 21 9 16.2 13.4 17.6 20 12 16.7 6.4 20 7.8 13.4 3 9 9.4 8.4z" fill="#FBBC05" stroke="#E2A803" stroke-width=".8" stroke-linejoin="round"/></svg>',
  email: '<svg width="36" height="36" viewBox="0 0 24 24"><rect x="3" y="6" width="18" height="13" rx="2.2" fill="#E97B2E"/><path d="M3 6h18l-9 6.4z" fill="#C76420"/><path d="M3.4 6.8l8.6 6 8.6-6" fill="none" stroke="#FFE6CD" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  chat: '<svg width="36" height="36" viewBox="0 0 24 24"><path d="M4 11.5c0-3.8 3.6-6.8 8-6.8s8 3 8 6.8-3.6 6.8-8 6.8c-1 0-2-.1-2.9-.4L5 19.4l1-3.4c-1.3-1.2-2-2.7-2-4.5z" fill="#2E8FA8"/><circle cx="9" cy="11.6" r="1" fill="#fff"/><circle cx="12" cy="11.6" r="1" fill="#fff"/><circle cx="15" cy="11.6" r="1" fill="#fff"/></svg>',
  blog: '<svg width="36" height="36" viewBox="0 0 24 24"><path d="M5.5 3.5h8L18.5 8.5V19a1.6 1.6 0 0 1-1.6 1.6H5.5A1.6 1.6 0 0 1 3.9 19V5.1a1.6 1.6 0 0 1 1.6-1.6z" fill="#D4A437"/><path d="M13.5 3.5V8.5h5z" fill="#A8821D"/><rect x="6.5" y="11.5" width="9.5" height="1.4" rx=".7" fill="#fff"/><rect x="6.5" y="14.2" width="9.5" height="1.4" rx=".7" fill="#fff"/><rect x="6.5" y="16.9" width="6" height="1.4" rx=".7" fill="#fff"/></svg>',
  social: '<svg width="36" height="36" viewBox="0 0 24 24"><defs><linearGradient id="igGrad" x1="0" y1="1" x2="1" y2="0"><stop offset="0" stop-color="#FDC56A"/><stop offset=".4" stop-color="#E1306C"/><stop offset=".75" stop-color="#A33AB1"/><stop offset="1" stop-color="#5851DB"/></linearGradient></defs><rect x="3" y="3" width="18" height="18" rx="5" fill="url(#igGrad)"/><circle cx="12" cy="12" r="4" fill="none" stroke="#fff" stroke-width="1.8"/><circle cx="17" cy="7" r="1.1" fill="#fff"/></svg>'
};

window.RINGS = [
  { id:'gbp',    label:'Google Business', svg:'google',  cred:'487 reviews · 2 locations' },
  { id:'seo',    label:'SEO',             svg:'seo',     cred:'3 sites tracked' },
  { id:'reviews',label:'Reviews',         svg:'reviews', cred:'Voice accepted' },
  { id:'email',  label:'Email assistant', svg:'email',   cred:'GHL connected' },
  { id:'chat',   label:'Chat widget',     svg:'chat',    cred:'1-line snippet' },
  { id:'blog',   label:'Blog',            svg:'blog',    cred:'WordPress detected' },
  { id:'social', label:'Social',          svg:'social',  cred:'Optional' }
];
