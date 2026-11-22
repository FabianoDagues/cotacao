(() => {
  const parent = document.getElementById('account-parent');
  const nature = document.getElementById('account-nature');
  const name = document.getElementById('account-name');
  parent.addEventListener('change', () => {
    const option = parent.selectedOptions[0];
    if (option.dataset.nature) nature.value = option.dataset.nature;
  });
  document.querySelectorAll('.add-child').forEach(button => {
    button.addEventListener('click', () => {
      parent.value = button.dataset.id;
      nature.value = button.dataset.nature;
      document.getElementById('account-create').scrollIntoView({behavior: 'smooth', block: 'start'});
      name.focus({preventScroll: true});
    });
  });
  const details = [...document.querySelectorAll('.account-tree details')];
  document.getElementById('expand-accounts').addEventListener('click', () => details.forEach(d => d.open = true));
  document.getElementById('collapse-accounts').addEventListener('click', () => details.forEach(d => d.open = false));
  const normalize = text => text.normalize('NFD').replace(/[\u0300-\u036f]/g, '').toLowerCase();
  const nodes = [...document.querySelectorAll('.account-node')];
  document.getElementById('account-search').addEventListener('input', event => {
    const term = normalize(event.target.value.trim());
    nodes.slice().reverse().forEach(node => {
      const match = normalize(node.dataset.path).includes(term);
      const childMatch = [...node.querySelectorAll('.account-node')].some(child => !child.hidden);
      node.hidden = !(match || childMatch);
      const branch = node.querySelector(':scope > details');
      if (term && branch && !node.hidden) branch.open = true;
    });
    document.getElementById('account-empty').hidden = nodes.some(node => !node.hidden);
  });
})();
