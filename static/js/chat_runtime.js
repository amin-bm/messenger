// اگر این فایل در <head> (قبل از رندر chat.html) لود شود، window.RtChat و
// window._rtchatGen قبل از اجرای اسکریپت داخلی چت آماده خواهند بود.
window._rtchatGen = window._rtchatGen || 0;

window.RtChat = (function () {
  'use strict';

  var _generation = window._rtchatGen || 0;
  var _initialized = false;

  var _docListeners = [];
  var _bodyListeners = [];
  var _winListeners = [];
  var _observers = [];
  var _timers = [];

  function docOn(event, handler, options) {
    var opts = options || false;
    document.addEventListener(event, handler, opts);
    _docListeners.push({ e: event, h: handler, o: opts });
  }

  function bodyOn(event, handler, options) {
    var opts = options || false;
    document.body.addEventListener(event, handler, opts);
    _bodyListeners.push({ e: event, h: handler, o: opts });
  }

  function winOn(event, handler, options) {
    var opts = options || false;
    window.addEventListener(event, handler, opts);
    _winListeners.push({ e: event, h: handler, o: opts });
  }

  function trackObserver(obs) {
    if (obs) _observers.push(obs);
    return obs;
  }

  function trackInterval(id) {
    _timers.push({ t: 'i', id: id });
    return id;
  }

  function trackTimeout(id) {
    _timers.push({ t: 't', id: id });
    return id;
  }

  // پاک‌سازیِ سبک که باید همیشه اجرا شود، حتی وقتی init() صدا زده نشده
  // (مثل چتِ سرور-رندر شده که هنگام باز شدن اپ نمایش داده می‌شود).
  function resetSharedState() {
    var portalIds = ['message_action_menu', 'forward_modal', 'file_viewer_modal'];
    for (var i = 0; i < portalIds.length; i++) {
      var el = document.getElementById(portalIds[i]);
      if (el && el.dataset.portaled === '1') {
        try { el.remove(); } catch (e) {}
      }
    }
    try {
      if (document.body) delete document.body.dataset.rtchatMsgMenuBound;
    } catch (e) {}
  }

  function cleanup() {
    _generation++;
    window._rtchatGen = _generation;

    // مهم: این بخش باید همیشه اجرا شود تا وضعیتِ چتِ سرور-رندر شده نشت نکند،
    // چون برای آن چت init() صدا زده نشده و _initialized برابر false است.
    resetSharedState();

    if (!_initialized) return;
    _initialized = false;

    var i;
    for (i = 0; i < _docListeners.length; i++) {
      try {
        document.removeEventListener(_docListeners[i].e, _docListeners[i].h, _docListeners[i].o);
      } catch (e) {}
    }
    _docListeners = [];

    for (i = 0; i < _bodyListeners.length; i++) {
      try {
        document.body.removeEventListener(_bodyListeners[i].e, _bodyListeners[i].h, _bodyListeners[i].o);
      } catch (e) {}
    }
    _bodyListeners = [];

    for (i = 0; i < _winListeners.length; i++) {
      try {
        window.removeEventListener(_winListeners[i].e, _winListeners[i].h, _winListeners[i].o);
      } catch (e) {}
    }
    _winListeners = [];

    for (i = 0; i < _observers.length; i++) {
      try { _observers[i].disconnect(); } catch (e) {}
    }
    _observers = [];

    for (i = 0; i < _timers.length; i++) {
      try {
        if (_timers[i].t === 'i') clearInterval(_timers[i].id);
        else clearTimeout(_timers[i].id);
      } catch (e) {}
    }
    _timers = [];

    // ✅ بستن WebSocket در htmx 2.x - روش درست
    (function closeStaleWS() {
      // روش اصلی: از htmx-internal-data
      var wsForm = document.getElementById('chat_message_form');
      if (wsForm) {
        try {
          var internalData = wsForm['htmx-internal-data'];
          if (internalData && internalData.webSocket) {
            internalData.webSocket.close(1000, 'navigated away');
            internalData.webSocket = null;
          }
        } catch (e) {}
      }

      // روش پشتیبان: از _wsInstances
      if (window._wsInstances && Array.isArray(window._wsInstances)) {
        window._wsInstances.forEach(function (ws) {
          if (!ws) return;
          if (ws.readyState === WebSocket.CLOSED || ws.readyState === WebSocket.CLOSING) return;
          try {
            var wsPath = new URL(ws.url).pathname;
            if (wsPath.includes('/ws/chatroom/')) {
              ws.close(1000, 'navigated away');
            }
          } catch (e) {}
        });
      }
    })();

    // audio cleanup
    var content = document.getElementById('tg-chat-content');
    if (content) {
      var audios = content.querySelectorAll('audio');
      for (i = 0; i < audios.length; i++) {
        try {
          audios[i].pause();
          audios[i].removeAttribute('src');
          audios[i].load();
        } catch (e) {}
      }
    }

    if (window._rtchatMediaRecorder) {
      try {
        if (window._rtchatMediaRecorder.state !== 'inactive') window._rtchatMediaRecorder.stop();
      } catch (e) {}
      window._rtchatMediaRecorder = null;
    }

    if (window.rtchatCurrentUploadXhr) {
      try { window.rtchatCurrentUploadXhr.abort(); } catch (e) {}
      delete window.rtchatCurrentUploadXhr;
    }

    delete window.rtchatClearReply;
    delete window.rtchatMarkOwnMessageScrollPending;
    delete window.rtchatEnsureDateSeparators;
    delete window.rtchatInitLazyImages;
  }

  function init() {
    var chatContainer = document.getElementById('chat_container');
    if (!chatContainer) return;

    // اگر قبلاً init شده بود (مثلاً هم روی load و هم روی afterSwap صدا خورد)،
    // دوباره راه‌اندازی نکن تا لیسنرها تکراری نشوند.
    if (_initialized) return;

    _initialized = true;
    // نسل جاری را به عنوان نسلِ همین چت ثبت کن تا isStale() درست کار کند.
    window._rtchatGen = _generation;

    var content = document.getElementById('tg-chat-content');
    if (content && window.htmx && typeof window.htmx.process === 'function') {
      window.htmx.process(content);
    }

    setTimeout(function () {
      if (typeof window.scrollToBottom === 'function') {
        window.scrollToBottom(0, 8);
      }
    }, 50);
  }

  function staleGuard() {
    var myGen = _generation;
    return function () {
      return window._rtchatGen !== myGen;
    };
  }

  return {
    init: init,
    cleanup: cleanup,
    staleGuard: staleGuard,
    docOn: docOn,
    bodyOn: bodyOn,
    winOn: winOn,
    trackObserver: trackObserver,
    trackInterval: trackInterval,
    trackTimeout: trackTimeout,
    generation: function () {
      return _generation;
    },
    isInitialized: function () {
      return _initialized;
    },
  };
})();

window._rtchatGen = window._rtchatGen || 0;
