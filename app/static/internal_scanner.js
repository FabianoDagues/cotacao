(() => {
  const root = document.querySelector('[data-scanner-target]');
  if (!root) return;
  const startButton = root.querySelector('.camera-open');
  const stopButton = root.querySelector('.camera-stop');
  const status = root.querySelector('.camera-status');
  const target = document.getElementById(root.dataset.scannerTarget);
  let scanner, running = false, starting = false, stopping = null, accepted = false, cancelRequested = false;
  function controls() {
    startButton.disabled = starting || running || Boolean(stopping);
    stopButton.hidden = !(starting || running || stopping);
    stopButton.disabled = Boolean(stopping);
  }
  async function stop() {
    cancelRequested = true;
    if (starting) return;
    if (stopping) return stopping;
    if (!running) { controls(); return; }
    stopping = scanner.stop();
    controls();
    try { await stopping; running = false; }
    finally { stopping = null; controls(); }
  }
  async function start() {
    if (starting || running || stopping) return;
    if (!window.isSecureContext || !navigator.mediaDevices?.getUserMedia) {
      status.textContent = 'A câmera exige HTTPS. Abra o endereço seguro do sistema ou digite o código.'; return;
    }
    if (typeof Html5Qrcode === 'undefined') {
      status.textContent = 'Não foi possível carregar o leitor. Confira a internet ou digite o código.'; return;
    }
    starting = true; accepted = false; cancelRequested = false; controls();
    status.textContent = 'Permita o acesso à câmera e aponte para o código de barras.';
    try {
      const formats = Html5QrcodeSupportedFormats;
      scanner ||= new Html5Qrcode('internal-reader', {formatsToSupport:[formats.EAN_13, formats.EAN_8, formats.UPC_A, formats.CODE_128, formats.CODE_39, formats.ITF], verbose:false});
      await scanner.start({facingMode:'environment'}, {fps:10}, async text => {
        if (accepted || starting || !running || cancelRequested) return;
        accepted = true;
        try { await stop(); }
        catch (_) { status.textContent = 'Código lido, mas a câmera não fechou. Feche esta página e digite o código.'; return; }
        target.value = text;
        target.dispatchEvent(new Event('input', {bubbles:true}));
        status.textContent = 'Código lido. Confira o produto antes de confirmar.';
        if (root.dataset.scannerSubmit) document.getElementById(root.dataset.scannerSubmit).requestSubmit();
        else target.focus();
      }, () => {});
      running = true;
    } catch (_) {
      status.textContent = 'Câmera indisponível. Confira a permissão no navegador ou digite o código.';
    } finally {
      starting = false;
      if (cancelRequested && running) {
        try { await stop(); } catch (_) { status.textContent = 'Recarregue a página para encerrar a câmera.'; }
      }
      controls();
    }
  }
  startButton.addEventListener('click', start);
  stopButton.addEventListener('click', () => stop().catch(() => {status.textContent='Recarregue a página para encerrar a câmera.';}));
  document.addEventListener('visibilitychange', () => {if(document.hidden) stop().catch(()=>{});});
  window.addEventListener('pagehide', () => {stop().catch(()=>{});});
})();
