(() => {
  const status = document.getElementById('camera-status');
  const stopButton = document.getElementById('stop-scan');
  const buttons = [document.getElementById('scan-search'), document.getElementById('scan-register')];
  let scanner = null, running = false, busy = false, accepted = false;
  async function stop() {
    if (running && scanner) { await scanner.stop(); running = false; }
    stopButton.hidden = true;
    buttons.forEach(b => b.disabled = false);
  }
  async function start(target) {
    if (busy || running) return;
    if (!window.isSecureContext || !navigator.mediaDevices) {
      status.textContent = 'A câmera exige HTTPS. Acesse o endereço seguro do sistema ou digite o código.'; return;
    }
    if (typeof Html5Qrcode === 'undefined') {
      status.textContent = 'Não foi possível carregar o leitor. Verifique a internet ou digite o código.'; return;
    }
    busy = true; accepted = false;
    buttons.forEach(b => b.disabled = true);
    status.textContent = 'Aguarde e permita o acesso à câmera.';
    document.getElementById('reader').scrollIntoView({block:'center',behavior:'smooth'});
    try {
      scanner = scanner || new Html5Qrcode('reader', {formatsToSupport:[Html5QrcodeSupportedFormats.EAN_13,Html5QrcodeSupportedFormats.EAN_8,Html5QrcodeSupportedFormats.UPC_A,Html5QrcodeSupportedFormats.ITF],verbose:false});
      await scanner.start({facingMode:'environment'}, {fps:10}, async text => {
        if (accepted || !running) return;
        accepted = true;
        try { await stop(); } catch (_) { status.textContent = 'Código lido. Feche a câmera antes de continuar.'; return; }
        document.getElementById(target).value = text;
        status.textContent = 'Código lido. Confira o produto antes de salvar ou adicionar.';
        if (target === 'search') document.getElementById('search-form').requestSubmit();
        else document.getElementById('barcode').scrollIntoView({block:'center',behavior:'smooth'});
      }, () => {});
      running = true; stopButton.hidden = false;
      status.textContent = 'Centralize o código de barras, com boa iluminação.';
    } catch (_) {
      status.textContent = 'Não foi possível abrir a câmera. Confira a permissão do navegador ou digite o código.';
      buttons.forEach(b => b.disabled = false);
    } finally { busy = false; }
  }
  buttons[0].addEventListener('click', () => start('search'));
  buttons[1].addEventListener('click', () => start('barcode'));
  stopButton.addEventListener('click', () => stop().catch(() => {status.textContent='Não foi possível fechar a câmera. Recarregue a página.';}));
  document.addEventListener('visibilitychange', () => {if(document.hidden && running) stop().catch(()=>{});});
})();
