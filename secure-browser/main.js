const { app, BrowserWindow, globalShortcut, Menu, ipcMain, dialog } = require('electron');
const path = require('path');
const fs = require('fs');

const CONFIG = { baseUrl: 'http://localhost' };

let mainWindow;
let isSafeToQuit = false;
let isExamActive = false;

const logPath = path.join(__dirname, '..', 'logs', 'oems_violations.log');

function writeLog(message) {
    const timestamp = new Date().toISOString();
    const line = `[${timestamp}] ${message}\n`;
    try { fs.appendFileSync(logPath, line); }
    catch (e) { console.error('Log write failed:', e); }
}

// ============================================================
// EXIT BUTTON — sirf non-exam pages pe inject hoga
// Exam page (start_exam) pe dikhega hi nahi
// ============================================================
function injectExitButton() {
    if (isExamActive) return; // exam chal raha hai — button mat dikhao

    const script = `
        (function() {
            if (document.getElementById('oems-exit-btn')) return;
            const btn = document.createElement('div');
            btn.id = 'oems-exit-btn';
            btn.innerHTML = '← Exit';
            btn.style.cssText = 'position:fixed;bottom:20px;right:20px;z-index:999999;background:rgba(239,68,68,0.15);color:#ef4444;border:1.5px solid rgba(239,68,68,0.4);padding:8px 18px;border-radius:20px;font-size:13px;font-weight:600;cursor:pointer;font-family:sans-serif;backdrop-filter:blur(10px);transition:all 0.2s;user-select:none;-webkit-user-select:none;';
            btn.addEventListener('mouseenter', () => { btn.style.background='rgba(239,68,68,0.3)'; btn.style.borderColor='#ef4444'; });
            btn.addEventListener('mouseleave', () => { btn.style.background='rgba(239,68,68,0.15)'; btn.style.borderColor='rgba(239,68,68,0.4)'; });
            btn.addEventListener('click', () => {
                if (window.secureBrowser && window.secureBrowser.requestExit) {
                    window.secureBrowser.requestExit();
                }
            });
            document.body.appendChild(btn);
        })();
    `;
    try { mainWindow.webContents.executeJavaScript(script); }
    catch (e) { console.error('[OEMS] Exit button inject failed:', e); }
}

app.whenReady().then(() => {
    Menu.setApplicationMenu(null);

    mainWindow = new BrowserWindow({
        fullscreen: true, kiosk: true, alwaysOnTop: true, frame: false,
        webPreferences: {
            nodeIntegration: false, contextIsolation: true,
            preload: path.join(__dirname, 'preload.js'),
            webSecurity: true, allowRunningInsecureContent: false, experimentalFeatures: false,
        }
    });

    // Secure browser signature
    mainWindow.webContents.session.webRequest.onBeforeSendHeaders((details, callback) => {
        details.requestHeaders['X-OEMS-Secure-Browser'] = 'ElectronV1';
        callback({ requestHeaders: details.requestHeaders });
    });

    mainWindow.loadFile('splash.html');
    writeLog('SESSION STARTED — OEMS Exam Browser launched.');

    mainWindow.webContents.on('did-finish-load', () => {
        // Copy/paste block — hamesha
        mainWindow.webContents.executeJavaScript(`
            document.addEventListener('contextmenu', e => e.preventDefault());
            document.addEventListener('copy',  e => e.preventDefault());
            document.addEventListener('cut',   e => e.preventDefault());
            document.addEventListener('paste', e => e.preventDefault());
            document.body.style.userSelect = 'none';
            document.body.style.webkitUserSelect = 'none';
        `);

        // Exit button — sirf non-exam pages pe
        injectExitButton();
    });

    mainWindow.webContents.on('will-navigate', (event, url) => {
        const allowed =
            url.startsWith('http://127.0.0.1') ||
            url.startsWith('http://localhost')  ||
            url.startsWith('file://');

        if (!allowed) {
            event.preventDefault();
            writeLog(`BLOCKED navigation to: ${url}`);
        }

        // Exam page detect karo
        const wasExamActive = isExamActive;
        isExamActive = url.includes('/start_exam/');

        if (isExamActive) writeLog(`Exam started: ${url}`);

        // Exam se bahar aa gaya — exit button wapas dikhao
        if (wasExamActive && !isExamActive) {
            setTimeout(() => injectExitButton(), 500);
        }
    });

    mainWindow.webContents.setWindowOpenHandler(({ url }) => {
        writeLog(`BLOCKED new window: ${url}`);
        return { action: 'deny' };
    });

    // ============================================================
    // KEYBOARD SHORTCUTS
    // NOTE: CommandOrControl+R (refresh) is NOT blocked anymore —
    //       refresh is now allowed as per requirement
    // ============================================================
    const blockedShortcuts = [
        'CommandOrControl+R',
        'Command+Option+W',
        'F5',
        'CommandOrControl+Shift+R',
        'F11',
        'F12',
        'CommandOrControl+Shift+I',
        'CommandOrControl+Shift+J',
        'CommandOrControl+W',
        'CommandOrControl+N',
        'CommandOrControl+T',
        'Alt+F4',
        'CommandOrControl+C',
        'CommandOrControl+V',
        'CommandOrControl+X',
        'CommandOrControl+A',
        'CommandOrControl+Option+Space',
        'CommandOrControl+Tab',
        'Alt+Tab',
        'CommandOrControl+M',
        'CommandOrControl+H',
    ];

    blockedShortcuts.forEach(key => {
        try { globalShortcut.register(key, () => { writeLog(`BLOCKED shortcut: ${key}`); }); }
        catch (err) { console.log(`Warning: Could not block ${key}`); }
    });
    writeLog(`Blocked ${blockedShortcuts.length} keyboard shortcuts. Refresh (Cmd+R/F5) is allowed.`);
});

// ============================================================
// IPC LISTENERS
// ============================================================

ipcMain.on('violation', (event, data) => {
    writeLog(`VIOLATION | type=${data.type} | details=${data.details}`);
});

ipcMain.on('submit-exam', (event) => {
    writeLog('EXAM SUBMITTED — safe quit triggered.');
    isExamActive = false;
    isSafeToQuit = true;
    setTimeout(() => app.quit(), 2000);
});

ipcMain.on('exam-started', (event) => {
    isExamActive = true;
    writeLog('Exam active — exit button hidden.');
});

// ============================================================
// EXIT BUTTON HANDLER — confirmation dialog with warning
// ============================================================
ipcMain.on('request-exit', async (event) => {

    // Confirmation dialog dikhao — native OS dialog
    const response = await dialog.showMessageBox(mainWindow, {
        type: 'warning',
        title: 'Exit OEMS Secure Browser',
        message: 'Are you sure you want to exit?',
        detail: 'Closing the browser will end your current session.\nMake sure you have completed your exam before exiting.',
        buttons: ['Cancel', 'Yes, Exit'],
        defaultId: 0,      // Default: Cancel
        cancelId: 0,       // Escape = Cancel
        icon: null
    });

    // response.response: 0 = Cancel, 1 = Yes Exit
    if (response.response === 0) {
        writeLog('Exit cancelled by student.');
        return; // Kuch mat karo
    }

    // Student ne confirm kiya — exit
    writeLog('EXIT CONFIRMED by student.');
    isSafeToQuit = true;
    app.quit();
});

// ============================================================
// FORCE QUIT BLOCKER
// ============================================================
app.on('before-quit', (event) => {
    if (!isSafeToQuit) {
        event.preventDefault();
        writeLog('BLOCKED force quit attempt.');
    }
});

process.on('SIGTERM', () => { writeLog('SIGTERM.'); isSafeToQuit = true; app.quit(); });
process.on('SIGINT',  () => { writeLog('SIGINT.');  isSafeToQuit = true; app.quit(); });

app.on('will-quit', () => {
    globalShortcut.unregisterAll();
    writeLog('SESSION ENDED.\n' + '='.repeat(60));
});