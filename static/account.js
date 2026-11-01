// Окна «Настройки и безопасность» (пароль, 2FA) и «Пользователи» (для администратора)

(function () {
    'use strict';

    // вспомогательные функции
    function h(tag, props, ...children) {
        const node = document.createElement(tag);
        for (const [key, value] of Object.entries(props || {})) {
            if (key === 'class') node.className = value;
            else if (key === 'text') node.textContent = value;
            else if (key.startsWith('on')) node.addEventListener(key.slice(2), value);
            else if (value === true) node.setAttribute(key, '');
            else if (value !== false && value != null) node.setAttribute(key, value);
        }
        for (const child of children.flat()) {
            if (child == null || child === false) continue;
            node.append(child.nodeType ? child : document.createTextNode(String(child)));
        }
        return node;
    }

    async function api(url, method, body) {
        const res = await fetch(url, {
            method: method || 'GET',
            headers: body ? {'Content-Type': 'application/json'} : undefined,
            body: body ? JSON.stringify(body) : undefined,
        });
        let data = {};
        try { data = await res.json(); } catch (e) { /* пустой ответ */ }
        if (!res.ok) {
            const detail = Array.isArray(data.detail) ? 'Проверьте введённые данные' : data.detail;
            throw new Error(detail || 'Ошибка запроса');
        }
        return data;
    }

    function openModal(title, content, opts = {}) {
        const wide = opts.wide;
        const box = h('div', {class: 'modal-box acc-modal' + (wide ? ' acc-modal-wide' : '')},
            h('h3', {class: 'modal-title', text: title}), content);
        const overlay = h('div', {class: 'modal-overlay acc-overlay open' + (opts.top ? ' acc-top' : '')}, box);
        const close = () => overlay.remove();
        overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
        document.body.append(overlay);
        return {overlay, box, close};
    }

    function message(kind) {
        const node = h('div', {class: 'acc-msg ' + kind});
        node.show = (text) => { node.textContent = text; node.style.display = 'block'; };
        node.hide = () => { node.style.display = 'none'; };
        node.hide();
        return node;
    }

    function askText(title, label, type) {
        return new Promise((resolve) => {
            const input = h('input', {class: 'modal-input', type: type || 'text', autocomplete: 'off', style: 'margin-bottom:16px'});
            const done = (value) => { modal.close(); resolve(value); };
            const body = h('div', {},
                h('label', {class: 'acc-label', text: label}), input,
                h('div', {class: 'modal-actions'},
                    h('button', {class: 'modal-btn', text: 'Отмена', onclick: () => done(null)}),
                    h('button', {class: 'modal-btn primary', text: 'Готово', onclick: () => done(input.value)})));
            const modal = openModal(title, body, {top: true});
            input.addEventListener('keydown', (e) => { if (e.key === 'Enter') done(input.value); });
            input.focus();
        });
    }

    // настройки и безопасность
    async function openSettings() {
        const me = await api('/api/me');
        const info = h('div', {class: 'acc-info'},
            h('div', {}, h('span', {text: 'Логин'}), h('b', {text: me.username + (me.is_admin ? ' (администратор)' : '')})),
            h('div', {}, h('span', {text: 'Занято в личных файлах'}),
                h('b', {text: me.used_human + (me.quota_gb ? ` из ${me.quota_gb} ГБ` : ' (без квоты)')})),
            me.cloud_readonly ? h('div', {}, h('span', {text: 'Режим'}), h('b', {text: 'только чтение'})) : null);

        // пароль
        const pwdCurrent = h('input', {class: 'modal-input', type: 'password', autocomplete: 'current-password', placeholder: 'Текущий пароль'});
        const pwdNew = h('input', {class: 'modal-input', type: 'password', autocomplete: 'new-password', placeholder: 'Новый пароль (от 8 символов)'});
        const pwdRepeat = h('input', {class: 'modal-input', type: 'password', autocomplete: 'new-password', placeholder: 'Повторите новый пароль'});
        const pwdMsg = message('');
        const pwdBtn = h('button', {class: 'modal-btn primary acc-solid', text: 'Сменить пароль', onclick: async () => {
            pwdMsg.hide();
            if (pwdNew.value !== pwdRepeat.value) { pwdMsg.className = 'acc-msg error'; return pwdMsg.show('Пароли не совпадают'); }
            try {
                await api('/api/me/password', 'POST', {current_password: pwdCurrent.value, new_password: pwdNew.value});
                pwdCurrent.value = pwdNew.value = pwdRepeat.value = '';
                pwdMsg.className = 'acc-msg ok'; pwdMsg.show('Пароль изменён. Остальные устройства придётся авторизовать заново.');
            } catch (err) { pwdMsg.className = 'acc-msg error'; pwdMsg.show(err.message); }
        }});

        const twofaBox = h('div', {});
        async function renderTwofa() {
            const {enabled} = await api('/api/2fa/status');
            twofaBox.replaceChildren();
            const msg = message('error');
            if (enabled) {
                const code = h('input', {class: 'modal-input', inputmode: 'numeric', maxlength: 6, placeholder: 'Код из приложения'});
                twofaBox.append(
                    h('p', {class: 'acc-hint', text: 'Двухфакторная аутентификация включена. Чтобы отключить, введите текущий код.'}),
                    code,
                    h('button', {class: 'modal-btn acc-solid', text: 'Отключить 2FA', onclick: async () => {
                        try { await api('/api/2fa/disable', 'POST', {code: code.value}); renderTwofa(); } catch (err) { msg.show(err.message); }
                    }}), msg);
            } else {
                twofaBox.append(
                    h('p', {class: 'acc-hint', text: 'Дополнительная защита входа: одноразовый код из приложения (Google Authenticator, Aegis, 2FAS и т. п.).'}),
                    h('button', {class: 'modal-btn primary acc-solid', text: 'Включить 2FA', onclick: async () => {
                        try {
                            const data = await api('/api/2fa/setup', 'POST');
                            twofaBox.replaceChildren();
                            const qr = h('div', {class: 'acc-qr'});
                            const code = h('input', {class: 'modal-input', inputmode: 'numeric', maxlength: 6, placeholder: 'Код из приложения'});
                            twofaBox.append(
                                h('p', {class: 'acc-hint', text: 'Отсканируйте QR-код в приложении и введите показанный код.'}),
                                qr,
                                h('div', {class: 'acc-secret'}, 'Секрет вручную: ', h('code', {text: data.secret})),
                                code,
                                h('button', {class: 'modal-btn primary acc-solid', text: 'Подтвердить и включить', onclick: async () => {
                                    try { await api('/api/2fa/enable', 'POST', {code: code.value}); renderTwofa(); } catch (err) { msg.show(err.message); }
                                }}), msg);
                            new QRCode(qr, {text: data.otpauth_url, width: 180, height: 180});
                        } catch (err) { msg.show(err.message); }
                    }}), msg);
            }
        }
        await renderTwofa();

        const content = h('div', {},
            info,
            h('h4', {class: 'acc-section', text: 'Смена пароля'}), pwdCurrent, pwdNew, pwdRepeat, pwdBtn, pwdMsg,
            h('h4', {class: 'acc-section', text: 'Двухфакторная аутентификация'}), twofaBox,
            h('div', {class: 'modal-actions', style: 'margin-top:18px'},
                h('button', {class: 'modal-btn', text: 'Закрыть', onclick: () => modal.close()})));
        const modal = openModal('Настройки и безопасность', content);
    }

    // пользователи (администратор)
    async function openUsers() {
        const listBox = h('div', {class: 'acc-users'});
        const msg = message('error');
        const content = h('div', {}, listBox, msg);
        const modal = openModal('Пользователи', content, {wide: true});

        const fmtDate = (iso) => iso ? new Date(iso + 'Z').toLocaleString('ru-RU', {dateStyle: 'short', timeStyle: 'short'}) : 'никогда';

        async function patch(username, body) {
            msg.hide();
            try { await api('/api/users/' + encodeURIComponent(username), 'PATCH', body); await reload(); }
            catch (err) { msg.show(err.message); await reload(); }
        }

        function row(u) {
            const quota = h('input', {class: 'acc-quota', type: 'number', min: 0, value: u.quota_gb, title: 'Квота, ГБ (0 = без ограничения)'});
            quota.addEventListener('change', () => patch(u.username, {quota_gb: Math.max(0, parseInt(quota.value, 10) || 0)}));
            const toggle = (label, field, value) => h('label', {class: 'acc-check'},
                h('input', Object.assign({type: 'checkbox', onchange: (e) => patch(u.username, {[field]: e.target.checked})}, value ? {checked: true} : {})), label);
            const isSelf = u.username === currentUser.username;
            return h('div', {class: 'acc-user' + (u.disabled ? ' off' : '')},
                h('div', {class: 'acc-user-head'},
                    h('b', {text: u.username}),
                    u.is_admin ? h('span', {class: 'acc-badge', text: 'админ'}) : null,
                    u.totp_enabled ? h('span', {class: 'acc-badge ok', text: '2FA'}) : null,
                    u.disabled ? h('span', {class: 'acc-badge bad', text: 'отключён'}) : null,
                    h('span', {class: 'acc-sub', text: `занято ${u.used_human} · вход: ${fmtDate(u.last_login_at)}`})),
                h('div', {class: 'acc-user-opts'},
                    h('label', {class: 'acc-check'}, 'Квота ГБ ', quota),
                    isSelf ? null : toggle('Админ', 'is_admin', u.is_admin),
                    toggle('Только чтение', 'cloud_readonly', u.cloud_readonly),
                    toggle('Может делиться', 'cloud_can_share', u.cloud_can_share),
                    isSelf ? null : toggle('Отключён', 'disabled', u.disabled)),
                h('div', {class: 'acc-user-actions'},
                    h('button', {class: 'modal-btn', text: 'Новый пароль', onclick: async () => {
                        const pw = await askText('Новый пароль для ' + u.username, 'Не короче 8 символов', 'password');
                        if (pw) patch(u.username, {password: pw});
                    }}),
                    u.totp_enabled ? h('button', {class: 'modal-btn', text: 'Сбросить 2FA', onclick: async () => {
                        try { await api(`/api/users/${encodeURIComponent(u.username)}/reset-2fa`, 'POST'); await reload(); } catch (err) { msg.show(err.message); }
                    }}) : null,
                    isSelf ? null : h('button', {class: 'modal-btn danger', text: 'Удалить', onclick: () => {
                        openDeleteModal('Удалить пользователя?', `Аккаунт «${u.username}» будет удалён. Файлы пользователя останутся на диске сервера (каталог personal/${u.username}).`, async () => {
                            try { await api('/api/users/' + encodeURIComponent(u.username), 'DELETE'); await reload(); } catch (err) { msg.show(err.message); }
                        });
                    }})));
        }

        function addForm() {
            const f = {
                username: h('input', {class: 'modal-input', placeholder: 'Логин', autocomplete: 'off'}),
                password: h('input', {class: 'modal-input', type: 'password', placeholder: 'Пароль (от 8 символов)', autocomplete: 'new-password'}),
                quota: h('input', {class: 'modal-input', type: 'number', min: 0, placeholder: 'Квота, ГБ', value: 0}),
                admin: h('input', {type: 'checkbox'}),
                readonly: h('input', {type: 'checkbox'}),
            };
            return h('div', {class: 'acc-add'},
                h('h4', {class: 'acc-section', text: 'Добавить пользователя'}),
                h('div', {class: 'acc-add-grid'}, f.username, f.password, f.quota),
                h('div', {class: 'acc-add-checks'},
                    h('label', {class: 'acc-check'}, f.admin, 'Администратор'),
                    h('label', {class: 'acc-check'}, f.readonly, 'Только чтение'),
                    h('button', {class: 'modal-btn primary acc-solid', text: 'Создать', onclick: async () => {
                        msg.hide();
                        try {
                            await api('/api/users', 'POST', {
                                username: f.username.value.trim(), password: f.password.value,
                                quota_gb: parseInt(f.quota.value, 10) || 0, is_admin: f.admin.checked, cloud_readonly: f.readonly.checked,
                            });
                            await reload();
                        } catch (err) { msg.show(err.message); }
                    }})));
        }

        async function reload() {
            const {users} = await api('/api/users');
            listBox.replaceChildren(...users.map(row), addForm());
        }
        content.append(h('div', {class: 'modal-actions', style: 'margin-top:14px'},
            h('button', {class: 'modal-btn', text: 'Закрыть', onclick: () => modal.close()})));
        await reload();
    }

    function wire() {
        const settings = document.getElementById('settingsMenuItem');
        const users = document.getElementById('usersMenuItem');
        if (settings) settings.addEventListener('click', () => openSettings().catch(console.error));
        if (users) users.addEventListener('click', () => openUsers().catch(console.error));
    }
    wire();
})();
