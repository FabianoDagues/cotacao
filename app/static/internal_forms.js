(() => {
  const form = document.getElementById('withdraw-save');
  if (form) {
    form.addEventListener('submit', () => {
      const button = form.querySelector('button[type="submit"]');
      button.disabled = true;
      button.textContent = 'Registrando…';
    });
    window.addEventListener('pageshow', () => {
      const button = form.querySelector('button[type="submit"]');
      button.disabled = false;
      button.textContent = 'Confirmar retirada';
    });
    document.getElementById('selected-product').scrollIntoView({block:'start',behavior:'smooth'});
  }
  document.getElementById('print-report')?.addEventListener('click', () => window.print());
})();
