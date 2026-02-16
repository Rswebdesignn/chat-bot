(function () {
    // ═══════════════════════════════════════════════
    // BPA-Flow Premium Widget Loader v3.0
    // ═══════════════════════════════════════════════
    const config = window.BPAFlowConfig || {};
    if (!config.chatbot_id) {
        console.error('BPAFlow: chatbot_id is required in BPAFlowConfig');
        return;
    }

    // --- Configuration Defaults ---
    const primaryColor = config.primaryColor || '#6366f1';
    const position = config.position || 'right';
    const iconClass = config.icon || 'bi-robot';
    const launcherText = config.launcherText || '';
    const radius = config.radius || '24px';
    const width = config.width || '420px';
    const height = config.height || '680px';
    const baseUrl = (config.baseUrl || window.location.origin).replace(/\/$/, '');

    // Derive lighter accent for gradients
    const accentGlow = primaryColor + '40';

    const iframeUrl = `${baseUrl}/chat/${config.chatbot_id}`;

    // --- Inject global styles & fonts ---
    const style = document.createElement('style');
    style.textContent = `
        @import url('https://cdn.jsdelivr.net/npm/bootstrap-icons@1.11.3/font/bootstrap-icons.css');
        @import url('https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600&display=swap');

        @keyframes bpaflow-breathe {
            0%, 100% { box-shadow: 0 6px 24px ${accentGlow}; }
            50% { box-shadow: 0 8px 36px ${primaryColor}60; }
        }
        @keyframes bpaflow-fadeSlide {
            from { opacity: 0; transform: translateX(${position === 'left' ? '-' : ''}12px); }
            to { opacity: 1; transform: translateX(0); }
        }
        @keyframes bpaflow-scaleIn {
            from { opacity: 0; transform: scale(0.85) translateY(20px); }
            to { opacity: 1; transform: scale(1) translateY(0); }
        }
        @keyframes bpaflow-float {
            0%, 100% { transform: translateY(0); }
            50% { transform: translateY(-3px); }
        }

        @media (max-width: 480px) {
            #bpaflow-chat-window {
                width: 100vw !important;
                height: 100vh !important;
                max-height: 100vh !important;
                max-width: 100vw !important;
                border-radius: 0 !important;
                margin: 0 !important;
                position: fixed !important;
                bottom: 0 !important;
                left: 0 !important;
                right: 0 !important;
            }
            #bpaflow-widget-container.is-open {
                bottom: 0 !important;
                left: 0 !important;
                right: 0 !important;
            }
        }

        #bpaflow-widget-container * {
            box-sizing: border-box;
        }
    `;
    document.head.appendChild(style);

    // --- Create Container ---
    const container = document.createElement('div');
    container.id = 'bpaflow-widget-container';
    container.style.cssText = `
        position: fixed;
        bottom: 24px;
        ${position}: 24px;
        z-index: 2147483647;
        font-family: 'Inter', -apple-system, sans-serif;
        display: flex;
        flex-direction: column;
        align-items: ${position === 'left' ? 'flex-start' : 'flex-end'};
        pointer-events: none;
    `;

    // --- Chat Window ---
    const chatWindow = document.createElement('div');
    chatWindow.id = 'bpaflow-chat-window';
    chatWindow.style.cssText = `
        width: ${width};
        height: ${height};
        max-width: calc(100vw - 48px);
        max-height: calc(100vh - 120px);
        background: #0a0a0f;
        border-radius: ${radius};
        box-shadow: 0 20px 60px rgba(0,0,0,0.3), 0 4px 16px rgba(0,0,0,0.15), 0 0 0 1px rgba(255,255,255,0.06);
        display: none;
        overflow: hidden;
        flex-direction: column;
        margin-bottom: 16px;
        pointer-events: auto;
        transform: scale(0.9) translateY(20px);
        opacity: 0;
        transition: transform 0.45s cubic-bezier(0.34, 1.56, 0.64, 1), opacity 0.35s ease;
        z-index: 2;
    `;

    const iframe = document.createElement('iframe');
    iframe.src = iframeUrl;
    iframe.style.cssText = `width: 100%; height: 100%; border: none; background: #0a0a0f;`;
    iframe.setAttribute('allow', 'clipboard-write');
    chatWindow.appendChild(iframe);

    // --- Launcher Row ---
    const launcherRow = document.createElement('div');
    launcherRow.style.cssText = `
        display: flex;
        align-items: center;
        gap: 12px;
        cursor: pointer;
        pointer-events: auto;
    `;
    if (position === 'left') launcherRow.style.flexDirection = 'row-reverse';

    // Launcher Label Pill
    const label = document.createElement('div');
    label.innerText = launcherText;
    label.style.cssText = `
        padding: 10px 18px;
        background: rgba(255,255,255,0.95);
        backdrop-filter: blur(12px);
        color: #111;
        border-radius: 100px;
        box-shadow: 0 4px 20px rgba(0,0,0,0.1), 0 0 0 1px rgba(0,0,0,0.04);
        font-size: 13px;
        font-weight: 600;
        display: ${launcherText ? 'block' : 'none'};
        white-space: nowrap;
        animation: bpaflow-fadeSlide 0.5s ease 1s both;
        letter-spacing: -0.01em;
        transition: all 0.3s ease;
    `;

    // Auto-hide label after 6 seconds
    if (launcherText) {
        setTimeout(() => {
            label.style.opacity = '0';
            label.style.transform = `translateX(${position === 'left' ? '-' : ''}12px)`;
            setTimeout(() => { label.style.display = 'none'; }, 300);
        }, 6000);
    }

    // Launcher Button
    const launcher = document.createElement('div');
    launcher.style.cssText = `
        width: 60px;
        height: 60px;
        background: linear-gradient(135deg, ${primaryColor}, ${primaryColor}cc);
        border-radius: 50%;
        display: flex;
        align-items: center;
        justify-content: center;
        color: white;
        font-size: 26px;
        box-shadow: 0 6px 24px ${accentGlow};
        transition: all 0.4s cubic-bezier(0.34, 1.56, 0.64, 1);
        position: relative;
        animation: bpaflow-breathe 3s ease-in-out infinite, bpaflow-float 4s ease-in-out infinite;
    `;
    launcher.innerHTML = `<i class="bi ${iconClass}"></i>`;

    launcherRow.appendChild(label);
    launcherRow.appendChild(launcher);

    // --- Mobile Fullscreen logic moved to CSS ---


    // --- Toggle Logic ---
    let isOpen = false;
    const toggleChat = () => {
        isOpen = !isOpen;
        if (isOpen) {
            container.classList.add('is-open');
            chatWindow.style.display = 'flex';
            // Force reflow for animation
            chatWindow.offsetHeight;
            requestAnimationFrame(() => {
                chatWindow.style.transform = 'scale(1) translateY(0)';
                chatWindow.style.opacity = '1';
            });
            launcher.innerHTML = '<i class="bi bi-x-lg"></i>';
            launcher.style.transform = 'rotate(90deg) scale(0.92)';
            launcher.style.animation = 'none';
            launcher.style.boxShadow = `0 4px 16px ${accentGlow}`;

            // Hide label
            label.style.display = 'none';
        } else {
            container.classList.remove('is-open');
            chatWindow.style.transform = 'scale(0.9) translateY(20px)';
            chatWindow.style.opacity = '0';
            setTimeout(() => { chatWindow.style.display = 'none'; }, 400);
            launcher.innerHTML = `<i class="bi ${iconClass}"></i>`;
            launcher.style.transform = 'rotate(0deg) scale(1)';
            launcher.style.animation = 'bpaflow-breathe 3s ease-in-out infinite, bpaflow-float 4s ease-in-out infinite';
            launcher.style.boxShadow = `0 6px 24px ${accentGlow}`;
        }
    };

    launcherRow.onclick = toggleChat;

    // --- Hover Effects ---
    launcherRow.onmouseenter = () => {
        if (!isOpen) {
            launcher.style.transform = 'scale(1.1)';
            launcher.style.boxShadow = `0 10px 36px ${primaryColor}55`;
        }
    };
    launcherRow.onmouseleave = () => {
        if (!isOpen) {
            launcher.style.transform = 'scale(1)';
            launcher.style.boxShadow = `0 6px 24px ${accentGlow}`;
        }
    };

    // --- Assemble & Inject ---
    container.appendChild(chatWindow);
    container.appendChild(launcherRow);
    document.body.appendChild(container);

})();
