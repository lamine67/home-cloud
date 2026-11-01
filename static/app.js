let currentUser = null;
let currentSpace = 'common';
let currentPath = '';
let allFolders = [];
let allFiles = [];
let sortKey = 'name';
let sortDir = 1;
let foldersPosition = 'top';
let selectedItems = new Map();

const SPACE_CONFIG = {
    common: {
        browse: '/api/browse',
        createFolder: '/api/folders',
        deleteFolder: '/api/folders',
        upload: '/api/upload',
        deleteFile: '/api/files',
        download: '/api/files/download',
        downloadFolder: '/api/folders/download',
        canManage: () => currentUser.shared_can_write,
        label: 'Общая папка',
    },
    personal: {
        browse: '/api/personal/browse',
        createFolder: '/api/personal/folders',
        deleteFolder: '/api/personal/folders',
        upload: '/api/personal/upload',
        deleteFile: '/api/personal/files',
        download: '/api/personal/files/download',
        downloadFolder: '/api/personal/folders/download',
        canManage: () => true,
        label: 'Мои файлы',
    },
};

const FILE_TYPE_MAP = {
    mp4: ['movie', '#ea4335'], mkv: ['movie', '#ea4335'], avi: ['movie', '#ea4335'],
    mov: ['movie', '#ea4335'], webm: ['movie', '#ea4335'], flv: ['movie', '#ea4335'],
    wmv: ['movie', '#ea4335'], m4v: ['movie', '#ea4335'],
    mp3: ['music_note', '#a142f4'], wav: ['music_note', '#a142f4'], flac: ['music_note', '#a142f4'],
    ogg: ['music_note', '#a142f4'], m4a: ['music_note', '#a142f4'], aac: ['music_note', '#a142f4'],
    wma: ['music_note', '#a142f4'],
    // изображения
    png: ['image', '#34a853'], jpg: ['image', '#34a853'], jpeg: ['image', '#34a853'],
    gif: ['image', '#34a853'], svg: ['image', '#34a853'], webp: ['image', '#34a853'], bmp: ['image', '#34a853'],
    // документы
    pdf: ['picture_as_pdf', '#ea4335'],
    doc: ['description', '#4285f4'], docx: ['description', '#4285f4'],
    txt: ['description', '#5f6368'], md: ['description', '#5f6368'], rtf: ['description', '#4285f4'],
    xls: ['table_chart', '#0f9d58'], xlsx: ['table_chart', '#0f9d58'], csv: ['table_chart', '#0f9d58'],
    ppt: ['slideshow', '#f9ab00'], pptx: ['slideshow', '#f9ab00'],
    // архивы
    zip: ['folder_zip', '#f9ab00'], rar: ['folder_zip', '#f9ab00'], '7z': ['folder_zip', '#f9ab00'],
    tar: ['folder_zip', '#f9ab00'], gz: ['folder_zip', '#f9ab00'],
    // образы дисков
    iso: ['draft', '#669df6'], img: ['draft', '#669df6'],
    // код и веб
    js: ['code', '#4285f4'], py: ['code', '#4285f4'], html: ['code', '#4285f4'], css: ['code', '#4285f4'],
    json: ['data_object', '#4285f4'], sh: ['code', '#4285f4'],
    // программы
    exe: ['terminal', '#5f6368'], apk: ['android', '#0f9d58'],
    // спец. изображения/дизайн
    heic: ['image', '#34a853'], psd: ['photo_library', '#4285f4'],
};

function esc(value) {
    return String(value ?? '').replace(/[&<>"']/g, ch => ({'&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'}[ch]));
}

function fileTypeIcon(name) {
    const ext = name.split('.').pop().toLowerCase();
    return FILE_TYPE_MAP[ext] || ['insert_drive_file', '#5f6368'];
}

function fileIconHtml(name, size) {
    const [icon, color] = fileTypeIcon(name);
    return `<span class="msi msi-filled" style="color:${color};font-size:${size}px;">${icon}</span>`;
}

async function loadMe() {
    const res = await fetch('/api/me');
    if (!res.ok) {
        window.location.href = '/login';
        return;
    }
    currentUser = await res.json();
    document.getElementById('avatarLetter').textContent = currentUser.username[0].toUpperCase();
    document.getElementById('userNameLabel').textContent = currentUser.username + (currentUser.is_admin ? ' (админ)' : '');
    if (currentUser.app_name) document.title = currentUser.app_name;
    const adminItem = document.getElementById('usersMenuItem');
    if (adminItem) adminItem.style.display = currentUser.is_admin ? '' : 'none';
}

async function loadStorage() {
    const res = await fetch('/api/storage');
    if (!res.ok) return;
    const data = await res.json();
    const quotaBytes = (data.quota_gb || 0) * 1024 ** 3;
    if (currentSpace === 'personal' && quotaBytes) {
        document.getElementById('storageFill').style.width = Math.min(100, data.personal_used / quotaBytes * 100) + '%';
        document.getElementById('storageText').textContent = `Ваши файлы: ${data.personal_used_human} из ${data.quota_gb} ГБ`;
    } else {
        document.getElementById('storageFill').style.width = data.percent + '%';
        document.getElementById('storageText').textContent = `Диск сервера: занято ${data.used_human} из ${data.total_human}`;
    }
}

function updateCreateVisibility() {
    const cfg = SPACE_CONFIG[currentSpace];
    const canManage = cfg && cfg.canManage();
    document.getElementById('createWrap').style.display = canManage ? 'block' : 'none';
    document.getElementById('newFolderHereBtn').style.display = canManage ? 'inline-flex' : 'none';
}

function renderBreadcrumbs() {
    const el = document.getElementById('breadcrumbs');
    el.innerHTML = '';
    const rootCrumb = document.createElement('span');
    rootCrumb.className = 'crumb' + (currentPath === '' ? ' current' : '');
    rootCrumb.textContent = SPACE_CONFIG[currentSpace].label;
    rootCrumb.addEventListener('click', () => navigateTo(''));
    el.appendChild(rootCrumb);

    const parts = currentPath ? currentPath.split('/') : [];
    let acc = '';
    parts.forEach((part, idx) => {
        const sep = document.createElement('span');
        sep.className = 'sep';
        sep.textContent = '›';
        el.appendChild(sep);

        acc = acc ? acc + '/' + part : part;
        const crumb = document.createElement('span');
        crumb.className = 'crumb' + (idx === parts.length - 1 ? ' current' : '');
        crumb.textContent = part;
        const target = acc;
        crumb.addEventListener('click', () => navigateTo(target));
        el.appendChild(crumb);
    });
}

function sortEntries(entries, isFolder) {
    return [...entries].sort((a, b) => {
        let cmp = 0;
        if (sortKey === 'name' || (isFolder && foldersPosition === 'top')) cmp = a.name.localeCompare(b.name);
        else if (sortKey === 'size') cmp = (a.size || 0) - (b.size || 0);
        else if (sortKey === 'date') cmp = (a.mtime || 0) - (b.mtime || 0);
        return cmp * sortDir;
    });
}

function itemKey(fullPath, isFolder) {
    return (isFolder ? 'd:' : 'f:') + fullPath;
}

function isSelected(fullPath, isFolder) {
    return selectedItems.has(itemKey(fullPath, isFolder));
}

const LARGE_FILE_BYTES = 300 * 1024 * 1024; // 300 МБ - крупнее не архивируем, качаем по отдельности

function toggleSelect(fullPath, isFolder, name, checked, size) {
    const key = itemKey(fullPath, isFolder);
    if (checked) {
        selectedItems.set(key, {path: fullPath, isFolder, name, size: size || 0});
    } else {
        selectedItems.delete(key);
    }
    updateSelectionUI();
}

function clearSelection() {
    selectedItems.clear();
    updateSelectionUI();
}

function updateSelectionUI() {
    const count = selectedItems.size;
    document.getElementById('normalToolbarRow').style.display = count > 0 ? 'none' : 'flex';
    document.getElementById('selectionToolbar').style.display = count > 0 ? 'flex' : 'none';
    document.getElementById('selectionCount').textContent = `выбрано ${count} ${count === 1 ? 'объект' : 'объекта'}`;
    document.querySelectorAll('.select-checkbox, .grid-select-checkbox').forEach(cb => {
        cb.checked = isSelected(cb.dataset.path, cb.dataset.isFolder === 'true');
    });
    document.querySelectorAll('tr[data-path]').forEach(tr => {
        tr.classList.toggle('selected', isSelected(tr.dataset.path, tr.dataset.isFolder === 'true'));
    });
    document.querySelectorAll('.file-card[data-path]').forEach(card => {
        card.classList.toggle('selected', isSelected(card.dataset.path, card.dataset.isFolder === 'true'));
    });
    const hasPersonalFile = currentSpace === 'personal' && [...selectedItems.values()].some(v => !v.isFolder);
    document.getElementById('bulkShareBtn').style.display = (currentSpace === 'personal' && count === 1 && hasPersonalFile) ? 'flex' : 'none';
}

function shareBadge(f) {
    if (currentSpace !== 'personal' || !f.shared_with || f.shared_with.length === 0) return '';
    const label = f.shared_with.includes('Все') ? 'Доступно всем' : `Доступно: ${f.shared_with.join(', ')}`;
    return `<span class="access-badge" title="${esc(label)}"><span class="msi" style="font-size:13px;">people</span> ${f.shared_with.length}</span>`;
}

function renderTable(folders, files) {
    const cfg = SPACE_CONFIG[currentSpace];
    const canManage = cfg.canManage();
    const tbody = document.getElementById('fileTableBody');
    tbody.innerHTML = '';
    if (folders.length === 0 && files.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6"><div class="empty-state">Здесь пока пусто</div></td></tr>';
        return;
    }
    const mixed = foldersPosition === 'mixed';
    const combined = mixed
        ? sortEntries([...folders.map(f => ({...f, __folder: true})), ...files], false)
        : null;

    const renderFolderRow = (f) => {
        const tr = document.createElement('tr');
        const fullPath = currentPath ? currentPath + '/' + f.name : f.name;
        tr.dataset.path = fullPath;
        tr.dataset.isFolder = 'true';
        tr.style.cursor = 'pointer';
        tr.innerHTML = `
            <td class="select-col"><input type="checkbox" class="select-checkbox" data-path="${esc(fullPath)}" data-is-folder="true"></td>
            <td><div class="file-name-cell"><div class="file-icon-sm"><span class="msi" style="font-size:16px;">folder</span></div><span>${esc(f.name)}</span></div></td>
            <td>${ownerLabel()}</td>
            <td>—</td><td>—</td>
            <td>
                <div class="row-actions">
                    <div class="icon-toolbar">
                        <button class="icon-btn download-folder-btn" title="Скачать"><span class="msi">download</span></button>
                    </div>
                    <button class="icon-btn more-btn" title="Ещё"><span class="msi">more_vert</span></button>
                </div>
            </td>
        `;
        tr.addEventListener('click', (e) => { if (!e.target.closest('.select-col') && !e.target.closest('.row-actions')) navigateTo(fullPath); });
        tr.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            showContextMenu(e.clientX, e.clientY, f, fullPath, true);
        });
        tr.querySelector('.select-checkbox').addEventListener('click', (e) => {
            e.stopPropagation();
            toggleSelect(fullPath, true, f.name, e.target.checked);
        });
        wireFolderDelete(tr, f, fullPath);
        tbody.appendChild(tr);
    };

    const renderFileRow = (f) => {
        const tr = document.createElement('tr');
        const fullPath = currentPath ? currentPath + '/' + f.name : f.name;
        tr.dataset.path = fullPath;
        tr.dataset.isFolder = 'false';
        tr.innerHTML = `
            <td class="select-col"><input type="checkbox" class="select-checkbox" data-path="${esc(fullPath)}" data-is-folder="false"></td>
            <td>
                <div class="file-name-cell">
                    <div class="file-icon-sm">${fileIconHtml(f.name, 20)}</div>
                    <span>${esc(f.name)}</span>
                    ${shareBadge(f)}
                </div>
            </td>
            <td>${ownerLabel()}</td>
            <td>${f.size_human}</td>
            <td>${f.modified}</td>
            <td>
                <div class="row-actions">
                    <div class="icon-toolbar">
                        ${currentSpace === 'personal' ? '<button class="icon-btn share-btn" title="Поделиться"><span class="msi">person_add</span></button>' : ''}
                        <button class="icon-btn download-btn" title="Скачать"><span class="msi">download</span></button>
                        ${canManage ? '<button class="icon-btn rename-btn" title="Переименовать"><span class="msi">edit</span></button>' : ''}
                    </div>
                    <button class="icon-btn more-btn" title="Ещё"><span class="msi">more_vert</span></button>
                </div>
            </td>
        `;
        tr.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            showContextMenu(e.clientX, e.clientY, f, fullPath, false);
        });
        tr.querySelector('.select-checkbox').addEventListener('click', (e) => {
            e.stopPropagation();
            toggleSelect(fullPath, false, f.name, e.target.checked, f.size);
        });
        wireRowActions(tr, f, fullPath);
        tbody.appendChild(tr);
    };

    if (mixed) {
        for (const f of combined) {
            if (f.__folder) renderFolderRow(f); else renderFileRow(f);
        }
    } else {
        for (const f of sortEntries(folders, true)) renderFolderRow(f);
        for (const f of sortEntries(files, false)) renderFileRow(f);
    }
    updateSelectionUI();
}

function renderGrid(folders, files) {
    const cfg = SPACE_CONFIG[currentSpace];
    const canManage = cfg.canManage();
    const grid = document.getElementById('fileGrid');
    grid.innerHTML = '';
    if (folders.length === 0 && files.length === 0) {
        grid.innerHTML = '<div class="empty-state">Здесь пока пусто</div>';
        return;
    }
    for (const f of sortEntries(folders, true)) {
        const fullPath = currentPath ? currentPath + '/' + f.name : f.name;
        const card = document.createElement('div');
        card.className = 'file-card';
        card.dataset.path = fullPath;
        card.dataset.isFolder = 'true';
        card.style.cursor = 'pointer';
        card.innerHTML = `
            <div class="grid-select-wrap"><input type="checkbox" class="grid-select-checkbox" data-path="${esc(fullPath)}" data-is-folder="true"></div>
            <div class="icon-row">
                <div class="file-icon"><span class="msi">folder</span></div>
                <div class="file-name">${esc(f.name)}</div>
            </div>
            <div class="card-actions">
                <div class="icon-toolbar">
                    <button class="icon-btn download-folder-btn" title="Скачать"><span class="msi">download</span></button>
                </div>
                <button class="icon-btn more-btn" title="Ещё"><span class="msi">more_vert</span></button>
            </div>
        `;
        card.addEventListener('click', (e) => { if (!e.target.closest('.grid-select-wrap') && !e.target.closest('.card-actions')) navigateTo(fullPath); });
        card.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            showContextMenu(e.clientX, e.clientY, f, fullPath, true);
        });
        card.querySelector('.grid-select-checkbox').addEventListener('click', (e) => {
            e.stopPropagation();
            toggleSelect(fullPath, true, f.name, e.target.checked);
        });
        wireFolderDelete(card, f, fullPath);
        grid.appendChild(card);
    }
    for (const f of sortEntries(files, false)) {
        const fullPath = currentPath ? currentPath + '/' + f.name : f.name;
        const card = document.createElement('div');
        card.className = 'file-card';
        card.dataset.path = fullPath;
        card.dataset.isFolder = 'false';
        card.innerHTML = `
            <div class="grid-select-wrap"><input type="checkbox" class="grid-select-checkbox" data-path="${esc(fullPath)}" data-is-folder="false"></div>
            <div class="icon-row">
                <div class="file-icon">${fileIconHtml(f.name, 28)}</div>
                <div class="file-name">${esc(f.name)}</div>
            </div>
            <div class="file-meta">
                <span>${f.size_human}</span>
                <span>${f.modified}</span>
            </div>
            ${shareBadge(f) ? `<div style="margin-top:8px;">${shareBadge(f)}</div>` : ''}
            <div class="card-actions">
                <div class="icon-toolbar">
                    ${currentSpace === 'personal' ? '<button class="icon-btn share-btn" title="Поделиться"><span class="msi">person_add</span></button>' : ''}
                    <button class="icon-btn download-btn" title="Скачать"><span class="msi">download</span></button>
                    ${canManage ? '<button class="icon-btn rename-btn" title="Переименовать"><span class="msi">edit</span></button>' : ''}
                </div>
                <button class="icon-btn more-btn" title="Ещё"><span class="msi">more_vert</span></button>
            </div>
        `;
        card.addEventListener('contextmenu', (e) => {
            e.preventDefault();
            showContextMenu(e.clientX, e.clientY, f, fullPath, false);
        });
        card.querySelector('.grid-select-checkbox').addEventListener('click', (e) => {
            e.stopPropagation();
            toggleSelect(fullPath, false, f.name, e.target.checked, f.size);
        });
        wireRowActions(card, f, fullPath);
        grid.appendChild(card);
    }
    updateSelectionUI();
}

function isConvertible(name) {
    const ext = name.split('.').pop().toLowerCase();
    return ['mp4','mkv','avi','mov','webm','flv','wmv','m4v','mp3','wav','flac','ogg','m4a','aac','wma'].includes(ext);
}

const deleteModalOverlay = document.getElementById('deleteModalOverlay');
const deleteModalTitle = document.getElementById('deleteModalTitle');
const deleteModalMessage = document.getElementById('deleteModalMessage');
let pendingDeleteAction = null;

function openDeleteModal(title, message, onConfirm) {
    deleteModalTitle.textContent = title;
    deleteModalMessage.textContent = message;
    pendingDeleteAction = onConfirm;
    deleteModalOverlay.classList.add('open');
}

function closeDeleteModal() {
    deleteModalOverlay.classList.remove('open');
    pendingDeleteAction = null;
}

document.getElementById('deleteCancelBtn').addEventListener('click', closeDeleteModal);
document.getElementById('deleteConfirmBtn').addEventListener('click', async () => {
    const action = pendingDeleteAction;
    closeDeleteModal();
    if (action) await action();
});
deleteModalOverlay.addEventListener('click', (e) => {
    if (e.target === deleteModalOverlay) closeDeleteModal();
});

async function doArchive(fullPath) {
    const r = await fetch('/api/personal/archive', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path: fullPath})
    });
    if (r.ok) {
        loadBrowse();
    } else {
        const d = await r.json();
        alert(d.detail || 'Ошибка архивации');
    }
}

async function doExtract(fullPath) {
    const r = await fetch('/api/personal/extract', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path: fullPath})
    });
    if (r.ok) {
        loadBrowse();
    } else {
        const d = await r.json();
        alert(d.detail || 'Ошибка распаковки');
    }
}

async function doCopy(fullPath) {
    const cfg = SPACE_CONFIG[currentSpace];
    const copyUrl = currentSpace === 'personal' ? '/api/personal/copy' : '/api/copy';
    const r = await fetch(copyUrl, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path: fullPath})
    });
    if (r.ok) {
        loadBrowse();
    } else {
        const d = await r.json();
        alert(d.detail || 'Ошибка копирования');
    }
}

function wireFolderDelete(el, f, fullPath) {
    const dlBtn = el.querySelector('.download-folder-btn');
    if (dlBtn) {
        dlBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const cfg = SPACE_CONFIG[currentSpace];
            window.location.href = `${cfg.downloadFolder}?path=${encodeURIComponent(fullPath)}`;
        });
    }
    const moreBtn = el.querySelector('.more-btn');
    if (moreBtn) {
        moreBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const r = moreBtn.getBoundingClientRect();
            showContextMenu(r.left, r.bottom, f, fullPath, true);
        });
    }
    const delBtn = el.querySelector('.delete-folder-btn');
    if (!delBtn) return;
    delBtn.addEventListener('click', (e) => {
        e.stopPropagation();
        const cfg = SPACE_CONFIG[currentSpace];
        openDeleteModal('Удалить папку?', `Папка "${f.name}" будет удалена со всем содержимым.`, async () => {
            const r = await fetch(`${cfg.deleteFolder}?path=${encodeURIComponent(fullPath)}`, {method: 'DELETE'});
            if (r.ok) {
                loadBrowse();
                loadShortcuts();
            } else {
                const d = await r.json();
                alert(d.detail || 'Ошибка удаления папки');
            }
        });
    });
}

function wireRowActions(el, f, fullPath) {
    const cfg = SPACE_CONFIG[currentSpace];
    el.querySelector('.download-btn').addEventListener('click', (e) => {
        e.stopPropagation();
        window.location.href = `${cfg.download}?path=${encodeURIComponent(fullPath)}`;
    });
    const shareBtn = el.querySelector('.share-btn');
    if (shareBtn) {
        shareBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            openShareModal(f.name, fullPath);
        });
    }
    const renameBtn = el.querySelector('.rename-btn');
    if (renameBtn) {
        renameBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            openRenameModal(f, fullPath, false);
        });
    }
    const moreBtn = el.querySelector('.more-btn');
    if (moreBtn) {
        moreBtn.addEventListener('click', (e) => {
            e.stopPropagation();
            const r = moreBtn.getBoundingClientRect();
            showContextMenu(r.left, r.bottom, f, fullPath, false);
        });
    }
}

function renderCurrentView() {
    renderTable(allFolders, allFiles);
    renderGrid(allFolders, allFiles);
}

async function loadBrowse() {
    const cfg = SPACE_CONFIG[currentSpace];
    const res = await fetch(`${cfg.browse}?path=${encodeURIComponent(currentPath)}`);
    if (!res.ok) {
        currentPath = '';
        return loadBrowse();
    }
    const data = await res.json();
    allFolders = data.folders;
    allFiles = data.files;
    renderBreadcrumbs();
    renderCurrentView();
    updateCreateVisibility();
    loadStorage();
}

function updateURL() {
    const params = new URLSearchParams();
    params.set('view', currentSpace);
    if ((currentSpace === 'common' || currentSpace === 'personal') && currentPath) {
        params.set('path', currentPath);
    }
    const url = '/?' + params.toString();
    if (location.pathname + location.search !== url) {
        history.pushState({view: currentSpace, path: currentPath}, '', url);
    }
}

function navigateTo(path, push = true) {
    currentPath = path;
    document.getElementById('searchInput').textContent = '';
    loadBrowse();
    if (push) updateURL();
}

async function loadShortcuts() {
    const res = await fetch('/api/shortcuts');
    if (!res.ok) return;
    const data = await res.json();
    const nav = document.getElementById('shortcutsNav');
    nav.innerHTML = '';
    for (const s of data.shortcuts) {
        const item = document.createElement('div');
        item.className = 'nav-item nav-item-shortcut';
        item.innerHTML = `
            <span style="display:flex;align-items:center;gap:10px;overflow:hidden;">
                <span class="msi">folder_special</span><span style="overflow:hidden;text-overflow:ellipsis;white-space:nowrap;">${esc(s.name)}</span>
            </span>
            <span class="msi remove-shortcut" title="Убрать из быстрого доступа">close</span>
        `;
        item.addEventListener('click', () => {
            switchViewTo(s.space || 'common', s.path);
        });
        item.querySelector('.remove-shortcut').addEventListener('click', (e) => {
            e.stopPropagation();
            openDeleteModal('Убрать из быстрого доступа?', `Ярлык "${s.name}" будет убран из быстрого доступа. Сама папка и её содержимое не удаляются.`, async () => {
                await fetch(`/api/shortcuts/${s.id}`, {method: 'DELETE'});
                loadShortcuts();
            });
        });
        nav.appendChild(item);
    }
}

async function loadSharedWithMe() {
    const res = await fetch('/api/shared-with-me');
    if (!res.ok) return;
    const data = await res.json();
    const tbody = document.getElementById('sharedTableBody');
    tbody.innerHTML = '';
    if (data.files.length === 0) {
        tbody.innerHTML = '<tr><td colspan="6"><div class="empty-state">Пока ничего не открыто для вас</div></td></tr>';
        return;
    }
    for (const f of data.files) {
        const tr = document.createElement('tr');
        tr.innerHTML = `
            <td><div class="file-name-cell"><div class="file-icon-sm">${fileIconHtml(f.name, 20)}</div><span>${esc(f.name)}</span></div></td>
            <td>${esc(f.owner)}</td>
            <td>${f.size_human}</td>
            <td>${f.modified}</td>
            <td>${f.is_public ? '<span class="access-badge">Всем</span>' : '<span class="access-badge">Лично вам</span>'}</td>
            <td><div class="row-actions"><button class="download-shared-btn">Скачать</button></div></td>
        `;
        tr.querySelector('.download-shared-btn').addEventListener('click', () => {
            window.location.href = `/api/shared/download?share_id=${f.share_id}`;
        });
        tbody.appendChild(tr);
    }
}

document.getElementById('avatarBtn').addEventListener('click', (e) => {
    document.getElementById('userMenu').classList.toggle('open');
    e.stopPropagation();
});
document.getElementById('createBtn').addEventListener('click', (e) => {
    document.getElementById('createMenu').classList.toggle('open');
    e.stopPropagation();
});
document.addEventListener('click', () => {
    document.getElementById('userMenu').classList.remove('open');
    document.getElementById('createMenu').classList.remove('open');
});
document.getElementById('logoutBtn').addEventListener('click', async () => {
    await fetch('/api/logout', {method: 'POST'});
    window.location.href = '/login';
});

const folderModalOverlay = document.getElementById('folderModalOverlay');
const folderNameInput = document.getElementById('folderNameInput');
let pinAsShortcut = false;

function openFolderModal(pinShortcut) {
    pinAsShortcut = pinShortcut;
    document.getElementById('createMenu').classList.remove('open');
    folderNameInput.value = 'Без названия';
    folderModalOverlay.classList.add('open');
    folderNameInput.focus();
    folderNameInput.select();
}

function closeFolderModal() {
    folderModalOverlay.classList.remove('open');
}

async function submitFolderModal() {
    const name = folderNameInput.value.trim();
    if (!name) return;
    const cfg = SPACE_CONFIG[currentSpace];
    // Левая кнопка "Создать" (pinAsShortcut=true) всегда создаёт папку в КОРНЕ раздела
    const targetPath = pinAsShortcut ? '' : currentPath;
    const res = await fetch(cfg.createFolder, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path: targetPath, name})
    });
    if (res.ok) {
        closeFolderModal();
        loadBrowse();
        if (pinAsShortcut) {
            await fetch('/api/shortcuts', {
                method: 'POST',
                headers: {'Content-Type': 'application/json'},
                body: JSON.stringify({name, path: name, space: currentSpace})
            });
            loadShortcuts();
        }
    } else {
        const d = await res.json();
        alert(d.detail || 'Ошибка создания папки');
    }
}

document.getElementById('newFolderItem').addEventListener('click', () => openFolderModal(true));
document.getElementById('newFolderHereBtn').addEventListener('click', () => openFolderModal(false));
document.getElementById('folderCancelBtn').addEventListener('click', closeFolderModal);
document.getElementById('folderCreateBtn').addEventListener('click', submitFolderModal);
folderModalOverlay.addEventListener('click', (e) => {
    if (e.target === folderModalOverlay) closeFolderModal();
});
folderNameInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') submitFolderModal();
    if (e.key === 'Escape') closeFolderModal();
});

async function uploadFiles(fileList, useRelativePath) {
    if (!fileList || fileList.length === 0) return;
    const cfg = SPACE_CONFIG[currentSpace];
    const formData = new FormData();
    formData.append('path', currentPath);
    for (const file of fileList) {
        formData.append('files', file);
        const rel = useRelativePath && file.webkitRelativePath ? file.webkitRelativePath : file.name;
        formData.append('paths', rel);
    }
    const res = await fetch(cfg.upload, {method: 'POST', body: formData});
    if (res.ok) {
        loadBrowse();
    } else {
        const d = await res.json();
        alert(d.detail || 'Ошибка загрузки');
    }
}

document.getElementById('uploadFileItem').addEventListener('click', () => {
    document.getElementById('createMenu').classList.remove('open');
    document.getElementById('fileUploadInput').click();
});
document.getElementById('uploadFolderItem').addEventListener('click', () => {
    document.getElementById('createMenu').classList.remove('open');
    document.getElementById('folderUploadInput').click();
});
document.getElementById('fileUploadInput').addEventListener('change', (e) => {
    uploadFiles(e.target.files, false);
    e.target.value = '';
});
document.getElementById('folderUploadInput').addEventListener('change', (e) => {
    uploadFiles(e.target.files, true);
    e.target.value = '';
});

function switchViewTo(view, path = '', push = true) {
    closeSidebarDrawer();
    document.querySelectorAll('.side-nav .nav-item').forEach(i => i.classList.remove('active'));
    const navItem = document.getElementById(view + 'NavItem');
    if (navItem) navItem.classList.add('active');

    document.getElementById('filesView').style.display = (view === 'common' || view === 'personal') ? 'block' : 'none';
    document.getElementById('sharedView').style.display = view === 'shared' ? 'block' : 'none';

    if (view === 'common' || view === 'personal') {
        currentSpace = view;
        currentPath = path;
        loadBrowse();
        if (push) updateURL();
    } else if (view === 'shared') {
        loadSharedWithMe();
        if (push) { currentSpace = 'shared'; updateURL(); }
    }
}

document.querySelectorAll('.side-nav > .nav-item').forEach(item => {
    item.addEventListener('click', () => switchViewTo(item.dataset.view));
});

// Mobile sidebar drawer
function openSidebarDrawer() {
    document.querySelector('.sidebar').classList.add('open');
    document.getElementById('sidebarBackdrop').classList.add('open');
}
function closeSidebarDrawer() {
    document.querySelector('.sidebar').classList.remove('open');
    document.getElementById('sidebarBackdrop').classList.remove('open');
}
document.getElementById('menuToggleBtn').addEventListener('click', openSidebarDrawer);
document.getElementById('sidebarBackdrop').addEventListener('click', closeSidebarDrawer);

window.addEventListener('popstate', (e) => {
    const params = new URLSearchParams(location.search);
    const view = params.get('view') || 'common';
    const path = params.get('path') || '';
    switchViewTo(view, path, false);
});

document.getElementById('searchInput').addEventListener('input', (e) => {
    const q = e.target.textContent.toLowerCase();
    const filteredFolders = allFolders.filter(f => f.name.toLowerCase().includes(q));
    const filteredFiles = allFiles.filter(f => f.name.toLowerCase().includes(q));
    renderTable(filteredFolders, filteredFiles);
    renderGrid(filteredFolders, filteredFiles);
});

const SORT_KEY_LABELS = {name: 'Название', size: 'Размер', date: 'Дата изменения'};

function updateSortUI() {
    document.querySelectorAll('[data-sort-key]').forEach(el => {
        el.classList.toggle('selected', el.dataset.sortKey === sortKey);
    });
    document.querySelectorAll('[data-sort-dir]').forEach(el => {
        el.classList.toggle('selected', Number(el.dataset.sortDir) === sortDir);
    });
    document.querySelectorAll('[data-folders-pos]').forEach(el => {
        el.classList.toggle('selected', el.dataset.foldersPos === foldersPosition);
    });
    document.querySelectorAll('.col-sort-icon').forEach(el => {
        el.textContent = el.dataset.iconFor === sortKey ? (sortDir === 1 ? 'arrow_upward' : 'arrow_downward') : '';
    });
}

const sortMenu = document.getElementById('sortMenu');
document.getElementById('sortToggleBtn').addEventListener('click', (e) => {
    e.stopPropagation();
    sortMenu.classList.toggle('open');
});
document.addEventListener('click', (e) => {
    if (!sortMenu.contains(e.target) && e.target.id !== 'sortToggleBtn' && !e.target.closest('#sortToggleBtn')) sortMenu.classList.remove('open');
});
sortMenu.addEventListener('click', (e) => {
    const item = e.target.closest('.sort-menu-item');
    if (!item) return;
    if (item.dataset.sortKey) sortKey = item.dataset.sortKey;
    if (item.dataset.sortDir) sortDir = Number(item.dataset.sortDir);
    if (item.dataset.foldersPos) foldersPosition = item.dataset.foldersPos;
    updateSortUI();
    renderCurrentView();
});
document.querySelectorAll('.sortable-col').forEach(col => {
    col.addEventListener('click', () => {
        const key = col.dataset.colKey;
        if (sortKey === key) {
            sortDir *= -1;
        } else {
            sortKey = key;
            sortDir = 1;
        }
        updateSortUI();
        renderCurrentView();
    });
});
updateSortUI();

document.getElementById('clearSelectionBtn').addEventListener('click', clearSelection);

document.getElementById('selectAllCheckbox').addEventListener('change', (e) => {
    const checked = e.target.checked;
    for (const f of allFolders) {
        const p = currentPath ? currentPath + '/' + f.name : f.name;
        toggleSelect(p, true, f.name, checked);
    }
    for (const f of allFiles) {
        const p = currentPath ? currentPath + '/' + f.name : f.name;
        toggleSelect(p, false, f.name, checked, f.size);
    }
});

document.getElementById('bulkDownloadBtn').addEventListener('click', () => {
    const cfg = SPACE_CONFIG[currentSpace];
    const items = [...selectedItems.values()];
    if (items.length === 1 && !items[0].isFolder) {
        window.location.href = `${cfg.download}?path=${encodeURIComponent(items[0].path)}`;
        return;
    }
    if (items.length === 1 && items[0].isFolder) {
        window.location.href = `${cfg.downloadFolder}?path=${encodeURIComponent(items[0].path)}`;
        return;
    }
    const hasLargeFile = items.some(i => !i.isFolder && i.size > LARGE_FILE_BYTES);
    if (hasLargeFile) {
        // Большие файлы не архивируем в общий zip - запускаем скачивание каждого элемента по отдельности одновременно
        for (const i of items) {
            const url = i.isFolder ? cfg.downloadFolder : cfg.download;
            const a = document.createElement('a');
            a.href = `${url}?path=${encodeURIComponent(i.path)}`;
            a.download = '';
            document.body.appendChild(a);
            a.click();
            a.remove();
        }
        return;
    }
    const url = currentSpace === 'personal' ? '/api/personal/bulk-download' : '/api/bulk-download';
    const params = items.map(i => `path=${encodeURIComponent(i.path)}`).join('&');
    window.location.href = `${url}?${params}`;
});

document.getElementById('bulkShareBtn').addEventListener('click', () => {
    const items = [...selectedItems.values()];
    if (items.length === 1) openShareModal(items[0].name, items[0].path);
});

document.getElementById('bulkMoveBtn').addEventListener('click', () => {
    const items = [...selectedItems.values()];
    openMoveModalBulk(items.map(i => i.path), items.length === 1 ? items[0].name : null);
});

document.getElementById('bulkDeleteBtn').addEventListener('click', () => {
    const items = [...selectedItems.values()];
    const n = items.length;
    openDeleteModal('Удалить выбранное?', `Будет удалено объектов: ${n}. Действие необратимо.`, async () => {
        const url = currentSpace === 'personal' ? '/api/personal/bulk-delete' : '/api/bulk-delete';
        const r = await fetch(url, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({paths: items.map(i => i.path)})
        });
        if (r.ok) {
            const d = await r.json();
            if (d.skipped && d.skipped.length) {
                alert(`Не удалось удалить: ${d.skipped.join(', ')}`);
            }
            clearSelection();
            loadBrowse();
        } else {
            alert('Ошибка удаления');
        }
    });
});

document.getElementById('listViewBtn').addEventListener('click', () => {
    document.getElementById('listViewBtn').classList.add('active');
    document.getElementById('gridViewBtn').classList.remove('active');
    document.getElementById('fileTable').style.display = '';
    document.getElementById('fileGrid').style.display = 'none';
});
document.getElementById('gridViewBtn').addEventListener('click', () => {
    document.getElementById('gridViewBtn').classList.add('active');
    document.getElementById('listViewBtn').classList.remove('active');
    document.getElementById('fileTable').style.display = 'none';
    document.getElementById('fileGrid').style.display = 'grid';
});

// Share dialog
const shareModalOverlay = document.getElementById('shareModalOverlay');
let shareCurrentPath = '';

async function openShareModal(name, fullPath) {
    shareCurrentPath = fullPath;
    document.getElementById('shareFileName').textContent = name;
    shareModalOverlay.classList.add('open');
    await refreshShareList();
}

async function refreshShareList() {
    const res = await fetch(`/api/shares?path=${encodeURIComponent(shareCurrentPath)}`);
    const data = await res.json();
    const list = document.getElementById('shareList');
    list.innerHTML = '';
    if (data.shares.length === 0) {
        list.innerHTML = '<div class="empty-state" style="padding:10px 0;">Пока ни с кем не поделено</div>';
    }
    for (const s of data.shares) {
        const item = document.createElement('div');
        item.className = 'share-list-item';
        item.innerHTML = `
            <span><span class="msi">${s.shared_with === 'Все' ? 'public' : 'person'}</span>${esc(s.shared_with)}</span>
            <span class="msi remove-share" title="Убрать доступ">close</span>
        `;
        item.querySelector('.remove-share').addEventListener('click', async () => {
            await fetch(`/api/shares/${s.id}`, {method: 'DELETE'});
            refreshShareList();
            loadBrowse();
        });
        list.appendChild(item);
    }
}

document.getElementById('shareAddUserBtn').addEventListener('click', async () => {
    const input = document.getElementById('shareUsernameInput');
    const username = input.value.trim();
    if (!username) return;
    const res = await fetch('/api/shares', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path: shareCurrentPath, shared_with: username})
    });
    if (res.ok) {
        input.value = '';
        refreshShareList();
        loadBrowse();
    } else {
        const d = await res.json();
        alert(d.detail || 'Ошибка');
    }
});

document.getElementById('sharePublicBtn').addEventListener('click', async () => {
    const res = await fetch('/api/shares', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path: shareCurrentPath, shared_with: null})
    });
    if (res.ok) {
        refreshShareList();
        loadBrowse();
    } else {
        const d = await res.json();
        alert(d.detail || 'Ошибка');
    }
});

document.getElementById('shareCloseBtn').addEventListener('click', () => {
    shareModalOverlay.classList.remove('open');
});
shareModalOverlay.addEventListener('click', (e) => {
    if (e.target === shareModalOverlay) shareModalOverlay.classList.remove('open');
});

let dragDepth = 0;
const dropOverlay = document.getElementById('dropOverlay');

function canUploadHere() {
    if (!currentUser) return false;
    const cfg = SPACE_CONFIG[currentSpace];
    return cfg && cfg.canManage();
}

window.addEventListener('dragenter', (e) => {
    if (!canUploadHere()) return;
    if (!e.dataTransfer.types.includes('Files')) return;
    e.preventDefault();
    dragDepth++;
    dropOverlay.classList.add('active');
});
window.addEventListener('dragover', (e) => {
    if (!canUploadHere()) return;
    if (!e.dataTransfer.types.includes('Files')) return;
    e.preventDefault();
});
window.addEventListener('dragleave', (e) => {
    if (!canUploadHere()) return;
    dragDepth = Math.max(0, dragDepth - 1);
    if (dragDepth === 0) dropOverlay.classList.remove('active');
});
window.addEventListener('drop', (e) => {
    if (!canUploadHere()) return;
    e.preventDefault();
    dragDepth = 0;
    dropOverlay.classList.remove('active');
    if (e.dataTransfer.files && e.dataTransfer.files.length > 0) {
        uploadFiles(e.dataTransfer.files, false);
    }
});

// Convert dialog
const convertModalOverlay = document.getElementById('convertModalOverlay');
const convertFormatSelect = document.getElementById('convertFormatSelect');
const convertSubmitBtn = document.getElementById('convertSubmitBtn');
let convertCurrentPath = '';
let convertCurrentName = '';

const VIDEO_FORMATS = ['mp4','mkv','avi','mov','webm'];
const AUDIO_FORMATS = ['mp3','wav','flac','ogg','m4a','aac'];

function openConvertModal(name, fullPath) {
    convertCurrentPath = fullPath;
    convertCurrentName = name;
    document.getElementById('convertFileName').textContent = name;
    document.getElementById('convertProgressMsg').style.display = 'none';
    const ext = name.split('.').pop().toLowerCase();
    const isVideo = VIDEO_FORMATS.includes(ext);
    const formats = isVideo ? VIDEO_FORMATS : AUDIO_FORMATS;
    convertFormatSelect.innerHTML = formats
        .filter(f => f !== ext)
        .map(f => `<option value="${f}">${f.toUpperCase()}</option>`)
        .join('');
    convertSubmitBtn.disabled = false;
    convertSubmitBtn.textContent = 'Конвертировать';
    convertModalOverlay.classList.add('open');
}

document.getElementById('convertCancelBtn').addEventListener('click', () => {
    convertModalOverlay.classList.remove('open');
});
convertModalOverlay.addEventListener('click', (e) => {
    if (e.target === convertModalOverlay) convertModalOverlay.classList.remove('open');
});

convertSubmitBtn.addEventListener('click', async () => {
    const targetExt = convertFormatSelect.value;
    convertSubmitBtn.disabled = true;
    convertSubmitBtn.textContent = 'Идёт конвертация...';
    document.getElementById('convertProgressMsg').style.display = 'block';
    const res = await fetch('/api/personal/convert', {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path: convertCurrentPath, target_ext: targetExt})
    });
    if (res.ok) {
        convertModalOverlay.classList.remove('open');
        loadBrowse();
    } else {
        const d = await res.json();
        alert(d.detail || 'Ошибка конвертации');
        convertSubmitBtn.disabled = false;
        convertSubmitBtn.textContent = 'Конвертировать';
        document.getElementById('convertProgressMsg').style.display = 'none';
    }
});

// Context menu
const contextMenu = document.getElementById('contextMenu');
let ctxItem = null, ctxPath = null, ctxIsFolder = false;

function showContextMenu(x, y, item, fullPath, isFolder) {
    ctxItem = item; ctxPath = fullPath; ctxIsFolder = isFolder;
    const cfg = SPACE_CONFIG[currentSpace];
    const canManage = cfg.canManage();
    const isPersonal = currentSpace === 'personal';

    document.getElementById('ctxShareItem').style.display = (isPersonal && !isFolder) ? 'flex' : 'none';
    document.getElementById('ctxArchiveItem').style.display = isPersonal ? 'flex' : 'none';
    document.getElementById('ctxExtractItem').style.display = (isPersonal && !isFolder && item.name.toLowerCase().endsWith('.zip')) ? 'flex' : 'none';
    document.getElementById('ctxConvertItem').style.display = (isPersonal && !isFolder && currentUser.can_convert && isConvertible(item.name)) ? 'flex' : 'none';

    const renameItem = contextMenu.querySelector('[data-action="rename"]');
    const copyItem = contextMenu.querySelector('[data-action="copy"]');
    const moveItem = contextMenu.querySelector('[data-action="move"]');
    const trashItem = contextMenu.querySelector('[data-action="trash"]');
    renameItem.classList.toggle('disabled', !canManage);
    copyItem.classList.toggle('disabled', !canManage);
    moveItem.classList.toggle('disabled', !canManage);
    trashItem.classList.toggle('disabled', !canManage);

    contextMenu.classList.add('open');
    const menuW = 260, menuH = 320;
    const posX = Math.min(x, window.innerWidth - menuW - 8);
    const posY = Math.min(y, window.innerHeight - menuH - 8);
    contextMenu.style.left = posX + 'px';
    contextMenu.style.top = posY + 'px';
}

function closeContextMenu() {
    contextMenu.classList.remove('open');
}

document.addEventListener('click', (e) => {
    if (!contextMenu.contains(e.target)) closeContextMenu();
});
document.addEventListener('scroll', closeContextMenu, true);

contextMenu.addEventListener('click', async (e) => {
    const itemEl = e.target.closest('.item');
    if (!itemEl || itemEl.classList.contains('disabled')) return;
    const action = itemEl.dataset.action;
    if (!action || action === 'organize') return;
    closeContextMenu();
    const cfg = SPACE_CONFIG[currentSpace];

    if (action === 'download') {
        const url = ctxIsFolder ? cfg.downloadFolder : cfg.download;
        window.location.href = `${url}?path=${encodeURIComponent(ctxPath)}`;
    } else if (action === 'rename') {
        openRenameModal(ctxItem, ctxPath, ctxIsFolder);
    } else if (action === 'copy') {
        doCopy(ctxPath);
    } else if (action === 'share') {
        openShareModal(ctxItem.name, ctxPath);
    } else if (action === 'archive') {
        doArchive(ctxPath);
    } else if (action === 'extract') {
        doExtract(ctxPath);
    } else if (action === 'convert') {
        openConvertModal(ctxItem.name, ctxPath);
    } else if (action === 'move') {
        openMoveModal(ctxPath, ctxItem.name);
    } else if (action === 'info') {
        openInfoModal(ctxItem, ctxPath, ctxIsFolder);
    } else if (action === 'trash') {
        const label = ctxIsFolder ? 'папку' : 'файл';
        openDeleteModal('Отправить в корзину?', `${ctxIsFolder ? 'Папка' : 'Файл'} "${ctxItem.name}" будет удалён без возможности восстановления.`, async () => {
            const url = ctxIsFolder ? cfg.deleteFolder : cfg.deleteFile;
            const r = await fetch(`${url}?path=${encodeURIComponent(ctxPath)}`, {method: 'DELETE'});
            if (r.ok) {
                loadBrowse();
                loadShortcuts();
            } else {
                const d = await r.json();
                alert(d.detail || 'Ошибка удаления');
            }
        });
    }
});

// Rename modal
const renameModalOverlay = document.getElementById('renameModalOverlay');
const renameNameInput = document.getElementById('renameNameInput');
let renameTargetPath = '', renameIsFolder = false;

function openRenameModal(item, fullPath, isFolder) {
    renameTargetPath = fullPath;
    renameIsFolder = isFolder;
    renameNameInput.value = item.name;
    renameModalOverlay.classList.add('open');
    renameNameInput.focus();
    const dot = item.name.lastIndexOf('.');
    if (!isFolder && dot > 0) {
        renameNameInput.setSelectionRange(0, dot);
    } else {
        renameNameInput.select();
    }
}

function closeRenameModal() {
    renameModalOverlay.classList.remove('open');
}

async function submitRename() {
    const newName = renameNameInput.value.trim();
    if (!newName) return;
    const renameUrl = currentSpace === 'personal' ? '/api/personal/rename' : '/api/rename';
    const res = await fetch(renameUrl, {
        method: 'POST',
        headers: {'Content-Type': 'application/json'},
        body: JSON.stringify({path: renameTargetPath, new_name: newName})
    });
    if (res.ok) {
        closeRenameModal();
        loadBrowse();
    } else {
        const d = await res.json();
        alert(d.detail || 'Ошибка переименования');
    }
}

document.getElementById('renameCancelBtn').addEventListener('click', closeRenameModal);
document.getElementById('renameSubmitBtn').addEventListener('click', submitRename);
renameModalOverlay.addEventListener('click', (e) => { if (e.target === renameModalOverlay) closeRenameModal(); });
renameNameInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter') submitRename();
    if (e.key === 'Escape') closeRenameModal();
});

// Move modal (folder picker)
const moveModalOverlay = document.getElementById('moveModalOverlay');
let moveTargetPaths = [];
let moveTargetName = '';
let pickerPath = '';

async function loadPicker() {
    const cfg = SPACE_CONFIG[currentSpace];
    const res = await fetch(`${cfg.browse}?path=${encodeURIComponent(pickerPath)}`);
    const data = res.ok ? await res.json() : {folders: []};
    const list = document.getElementById('movePickerList');
    list.innerHTML = '';

    if (pickerPath !== '') {
        const upItem = document.createElement('div');
        upItem.className = 'move-picker-item';
        upItem.innerHTML = `<span class="msi">arrow_upward</span> .. (на уровень выше)`;
        upItem.addEventListener('click', () => {
            const parts = pickerPath.split('/');
            parts.pop();
            pickerPath = parts.join('/');
            loadPicker();
        });
        list.appendChild(upItem);
    }

    const folders = data.folders.filter(f => !moveTargetPaths.includes(pickerPath ? pickerPath + '/' + f.name : f.name));
    if (folders.length === 0 && pickerPath === '') {
        list.innerHTML += '<div class="move-picker-empty">Нет вложенных папок</div>';
    }
    for (const f of folders) {
        const item = document.createElement('div');
        item.className = 'move-picker-item';
        item.innerHTML = `<span class="msi">folder</span> ${esc(f.name)}`;
        item.addEventListener('click', () => {
            pickerPath = pickerPath ? pickerPath + '/' + f.name : f.name;
            loadPicker();
        });
        list.appendChild(item);
    }

    const crumbs = document.getElementById('movePickerBreadcrumbs');
    crumbs.innerHTML = '';
    const rootCrumb = document.createElement('span');
    rootCrumb.className = 'crumb' + (pickerPath === '' ? '' : '');
    rootCrumb.textContent = SPACE_CONFIG[currentSpace].label;
    rootCrumb.addEventListener('click', () => { pickerPath = ''; loadPicker(); });
    crumbs.appendChild(rootCrumb);
    const parts = pickerPath ? pickerPath.split('/') : [];
    let acc = '';
    parts.forEach(part => {
        const sep = document.createElement('span');
        sep.className = 'sep';
        sep.textContent = '›';
        crumbs.appendChild(sep);
        acc = acc ? acc + '/' + part : part;
        const target = acc;
        const crumb = document.createElement('span');
        crumb.className = 'crumb';
        crumb.textContent = part;
        crumb.addEventListener('click', () => { pickerPath = target; loadPicker(); });
        crumbs.appendChild(crumb);
    });
}

function openMoveModal(fullPath, name) {
    moveTargetPaths = [fullPath];
    moveTargetName = name;
    startMovePicker();
}

function openMoveModalBulk(paths, name) {
    moveTargetPaths = paths;
    moveTargetName = name || `${paths.length} объектов`;
    startMovePicker();
}

function startMovePicker() {
    document.getElementById('moveModalTitle').textContent = `Перемещение объекта "${moveTargetName}"`;
    document.getElementById('moveCurrentChip').textContent = SPACE_CONFIG[currentSpace].label;
    pickerPath = '';
    moveModalOverlay.classList.add('open');
    loadPicker();
}

function closeMoveModal() {
    moveModalOverlay.classList.remove('open');
}

async function submitMove() {
    const dest = pickerPath;
    if (moveTargetPaths.length === 1) {
        const moveUrl = currentSpace === 'personal' ? '/api/personal/move' : '/api/move';
        const res = await fetch(moveUrl, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({path: moveTargetPaths[0], dest_folder: dest})
        });
        if (res.ok) {
            closeMoveModal();
            loadBrowse();
        } else {
            const d = await res.json();
            alert(d.detail || 'Ошибка перемещения');
        }
    } else {
        const moveUrl = currentSpace === 'personal' ? '/api/personal/bulk-move' : '/api/bulk-move';
        const res = await fetch(moveUrl, {
            method: 'POST',
            headers: {'Content-Type': 'application/json'},
            body: JSON.stringify({paths: moveTargetPaths, dest_folder: dest})
        });
        if (res.ok) {
            closeMoveModal();
            clearSelection();
            loadBrowse();
        } else {
            alert('Ошибка перемещения');
        }
    }
}

document.querySelectorAll('.move-tab').forEach(tab => {
    tab.addEventListener('click', () => {
        document.querySelectorAll('.move-tab').forEach(t => t.classList.remove('active'));
        tab.classList.add('active');
        if (tab.dataset.tab === 'recommended') {
            document.getElementById('movePickerBreadcrumbs').style.display = 'none';
            const list = document.getElementById('movePickerList');
            list.innerHTML = '<div class="move-picker-empty">Нет недавних папок</div>';
        } else {
            document.getElementById('movePickerBreadcrumbs').style.display = 'flex';
            loadPicker();
        }
    });
});

document.getElementById('moveCancelBtn').addEventListener('click', closeMoveModal);
document.getElementById('moveSubmitBtn').addEventListener('click', submitMove);
moveModalOverlay.addEventListener('click', (e) => { if (e.target === moveModalOverlay) closeMoveModal(); });

// Info modal
const infoModalOverlay = document.getElementById('infoModalOverlay');

function ownerLabel() {
    if (currentSpace === 'personal') return currentUser.username;
    if (currentSpace === 'common') return 'общая папка';
    return currentUser.username;
}

function openInfoModal(item, fullPath, isFolder) {
    const rows = document.getElementById('infoRows');
    const parts = [
        ['Название', item.name],
        ['Владелец', ownerLabel()],
        ['Тип', isFolder ? 'Папка' : 'Файл'],
        ['Путь', '/' + fullPath],
    ];
    if (!isFolder) {
        parts.push(['Размер', item.size_human]);
        parts.push(['Изменено', item.modified]);
    }
    rows.innerHTML = parts.map(([label, value]) =>
        `<div class="info-row"><span class="label">${label}</span><span class="value">${esc(value)}</span></div>`
    ).join('');
    infoModalOverlay.classList.add('open');
}

document.getElementById('infoCloseBtn').addEventListener('click', () => {
    infoModalOverlay.classList.remove('open');
});
infoModalOverlay.addEventListener('click', (e) => { if (e.target === infoModalOverlay) infoModalOverlay.classList.remove('open'); });

loadMe().then(() => {
    const params = new URLSearchParams(location.search);
    const view = params.get('view') || 'common';
    const path = params.get('path') || '';
    history.replaceState({view, path}, '', '/?' + (() => {
        const p = new URLSearchParams();
        p.set('view', view);
        if (path) p.set('path', path);
        return p.toString();
    })());
    switchViewTo(view, path, false);
    loadShortcuts();
});
