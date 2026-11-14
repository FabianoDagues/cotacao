(() => {
  const form = document.getElementById('withdraw-save');
  const quantity = document.getElementById('withdraw-quantity');
  if (quantity) {
    const minus = document.getElementById('qty-minus');
    const plus = document.getElementById('qty-plus');
    const read = () => /^\d{1,10}([.,]\d{1,4})?$/.test(quantity.value)
      ? Math.round(Number(quantity.value.replace(',', '.')) * 10000) : NaN;
    const update = () => {
      const value = read();
      quantity.setCustomValidity(value > 0 ? '' : 'Informe uma quantidade maior que zero.');
      minus.disabled = !(value > 10000);
      plus.disabled = value >= 99999999990000;
    };
    const step = direction => {
      const value = read();
      const next = (Number.isFinite(value) ? value : 0) + direction * 10000;
      if (next <= 0 || next >= 100000000000000) return;
      quantity.value = String(next / 10000).replace('.', ',');
      update();
    };
    minus.addEventListener('click', () => step(-1));
    plus.addEventListener('click', () => step(1));
    quantity.addEventListener('input', update);
    quantity.addEventListener('blur', () => {
      if (quantity.value.trim() && Number(quantity.value.replace(',', '.')) <= 0) {
        quantity.value = '1';
      }
      update();
    });
    let firstClick = false;
    quantity.addEventListener('focus', () => { quantity.select(); firstClick = true; });
    quantity.addEventListener('click', () => {
      if (firstClick) quantity.select();
      firstClick = false;
    });
    quantity.addEventListener('input', () => { firstClick = false; });
    update();
  }
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
