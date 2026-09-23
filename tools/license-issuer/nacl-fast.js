// TweetNaCl.js stub - license-issuer.html now uses WebCrypto Ed25519 and does not require this file.
// For offline use with old HTML, download full nacl-fast.js from:
// https://raw.githubusercontent.com/dchest/tweetnacl-js/master/nacl-fast.js
// or https://cdnjs.cloudflare.com/ajax/libs/tweetnacl/1.0.3/nacl-fast.js
// and place it here as nacl-fast.js (public domain, ~100KB).
// Minimal compatibility stub to avoid ReferenceError in old HTML:
(function(root){
  if (!root.nacl) root.nacl = {};
  // Provide minimal API that throws informative error if old HTML tries to use it
  function notAvailable() { throw new Error('nacl-fast.js stub: please download full TweetNaCl.js or use new license-issuer.html with WebCrypto'); }
  root.nacl.sign = root.nacl.sign || {};
  root.nacl.sign.keyPair = root.nacl.sign.keyPair || notAvailable;
  root.nacl.sign.keyPair.fromSeed = root.nacl.sign.keyPair.fromSeed || notAvailable;
  root.nacl.sign.detached = root.nacl.sign.detached || notAvailable;
  root.nacl.sign.detached.verify = root.nacl.sign.detached.verify || notAvailable;
})(typeof self !== 'undefined' ? self : typeof window !== 'undefined' ? window : this);
