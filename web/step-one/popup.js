const text = (vi, en) => localStorage.getItem('voxmeet-ui-language') === 'en' ? en : vi;
let showing = false;

export function popup({title, message = '', value, destructive = false}) {
  if (showing) return Promise.resolve(null);
  showing = true;
  const previous = document.activeElement;
  return new Promise(resolve => {
    const dialog = document.createElement('dialog');
    dialog.className = 'vox-popup';
    const form = document.createElement('form');
    const heading = document.createElement('h2');
    heading.id = 'vox-popup-title';
    heading.textContent = title;
    dialog.setAttribute('aria-labelledby', heading.id);
    form.append(heading);
    if (message) {
      const paragraph = document.createElement('p');
      paragraph.textContent = message;
      form.append(paragraph);
    }
    let input;
    if (value !== undefined) {
      const label = document.createElement('label');
      label.textContent = text('Tên bản ghi', 'Recording name');
      input = document.createElement('input');
      input.type = 'text';
      input.value = value;
      input.required = true;
      input.maxLength = 150;
      input.autocomplete = 'off';
      label.append(input);
      form.append(label);
      input.oninput = () => input.setCustomValidity('');
    }
    const actions = document.createElement('div');
    actions.className = 'popup-actions';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.className = 'button secondary';
    cancel.textContent = text('Hủy', 'Cancel');
    const submit = document.createElement('button');
    submit.type = 'submit';
    submit.className = 'button' + (destructive ? ' popup-danger' : '');
    submit.textContent = value !== undefined ? text('Lưu', 'Save') : destructive ? text('Xóa', 'Delete') : text('Tiếp tục', 'Continue');
    actions.append(cancel, submit);
    form.append(actions);
    dialog.append(form);
    let result = null;
    cancel.onclick = () => dialog.close();
    form.onsubmit = event => {
      event.preventDefault();
      if (input && !input.value.trim()) {
        input.setCustomValidity(text('Vui lòng nhập tên bản ghi.', 'Enter a recording name.'));
        input.reportValidity();
        return;
      }
      result = input ? input.value.trim() : true;
      dialog.close();
    };
    dialog.addEventListener('close', () => {
      dialog.remove();
      showing = false;
      if (previous?.isConnected) previous.focus();
      resolve(result);
    }, {once:true});
    document.body.append(dialog);
    dialog.showModal();
    if (input) { input.focus(); input.select(); }
    else cancel.focus();
  });
}
