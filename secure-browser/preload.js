const { contextBridge, ipcRenderer } = require('electron');

contextBridge.exposeInMainWorld('secureBrowser', {

    reportViolation: (type, details) => {
        if (typeof type !== 'string' || typeof details !== 'string') {
            console.warn('[OEMS] reportViolation: invalid input ignored.');
            return;
        }
        ipcRenderer.send('violation', {
            type:    type.slice(0, 50),
            details: details.slice(0, 200)
        });
    },

    submitExam: () => {
        ipcRenderer.send('submit-exam');
    },

    examStarted: () => {
        ipcRenderer.send('exam-started');
    },

    // EXIT BUTTON — user ne exit click kiya
    requestExit: () => {
        ipcRenderer.send('request-exit');
    },

    isElectron: true,
});