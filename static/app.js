// === ASMR-J2C 前端主逻辑 (修复版) ===
(function() {
    console.log("ASMR-J2C frontend loaded");

    // DOM 元素
    const form = document.querySelector("#jobForm");
    const startButton = document.querySelector("#startButton");
    const pauseButton = document.querySelector("#pauseButton");
    const resumeButton = document.querySelector("#resumeButton");
    const cancelButton = document.querySelector("#cancelButton");
    const serviceStatus = document.querySelector("#serviceStatus");
    const stageText = document.querySelector("#stageText");
    const countText = document.querySelector("#countText");
    const progressBar = document.querySelector("#progressBar");
    const currentText = document.querySelector("#currentText");
    const errorText = document.querySelector("#errorText");
    const downloadLink = document.querySelector("#downloadLink");
    const player = document.querySelector("#player");
    const preview = document.querySelector("#preview");
    const warnings = document.querySelector("#warnings");
    const emoControl = document.querySelector('[name="emo_control"]');
    const emotionTextInput = document.querySelector('[name="emo_text"]');
    const emotionVectorInputs = [...document.querySelectorAll('[name^="emo_vec_"]')];
    const appVersionSpan = document.querySelector("#appVersion");
    const ttsUrlInput = document.querySelector("#ttsUrl");
    const testTtsUrlBtn = document.querySelector("#testTtsUrl");
    const autoPlayWhenDone = document.querySelector("#autoPlayWhenDone");

    const baseTips = [
        "暂停会在当前句生成完成后生效，不会强行打断 IndexTTS2。",
        "如果页面没有显示版本号，请先运行 stop.bat 再重新启动。",
        "每次任务的实际 TTS 参数会写入 runtime.log。",
    ];

    let activeJobId = null;
    let pollTimer = null;

    // Toast 通知函数
    function showToast(message, type = "info") {
        const toast = document.createElement("div");
        toast.className = `toast toast-${type}`;
        toast.textContent = message;
        toast.style.position = "fixed";
        toast.style.bottom = "20px";
        toast.style.left = "50%";
        toast.style.transform = "translateX(-50%)";
        toast.style.backgroundColor = type === "error" ? "#dc3545" : (type === "success" ? "#28a745" : "#17a2b8");
        toast.style.color = "white";
        toast.style.padding = "10px 20px";
        toast.style.borderRadius = "8px";
        toast.style.zIndex = "10000";
        toast.style.fontSize = "14px";
        toast.style.boxShadow = "0 2px 10px rgba(0,0,0,0.2)";
        toast.style.transition = "opacity 0.3s";
        document.body.appendChild(toast);
        setTimeout(() => {
            toast.style.opacity = "0";
            setTimeout(() => toast.remove(), 300);
        }, 3000);
    }

    // 辅助：显示错误到界面
    function showError(message) {
        serviceStatus.textContent = "失败";
        errorText.textContent = message;
    }

    // 读取 JSON 响应
    async function readJson(response) {
        const payload = await response.json().catch(() => ({}));
        if (!response.ok) {
            throw new Error(payload.detail || `请求失败：${response.status}`);
        }
        return payload;
    }

    // 状态标签
    function statusLabel(status) {
        const map = {
            queued: "排队中",
            running: "处理中",
            paused: "已暂停",
            completed: "完成",
            failed: "失败",
            cancelled: "已取消",
        };
        return map[status] || "待上传";
    }

    // 格式化毫秒
    function formatMs(ms) {
        const totalSeconds = Math.floor(ms / 1000);
        const minutes = Math.floor(totalSeconds / 60);
        const seconds = totalSeconds % 60;
        const millis = String(ms % 1000).padStart(3, "0");
        return `${String(minutes).padStart(2, "0")}:${String(seconds).padStart(2, "0")}.${millis}`;
    }

    // 情感控制UI联动
    function syncEmotionControls() {
        if (!emoControl) return;
        const mode = emoControl.value;
        if (emotionTextInput) emotionTextInput.closest("label").classList.toggle("is-active", mode === "使用情感描述文本控制");
        emotionVectorInputs.forEach(input => {
            input.closest("label").classList.toggle("is-active", mode === "使用情感向量控制");
        });
    }

    // 验证 TTS 选项
    function validateTtsOptions(body) {
        const mode = body.get("emo_control");
        if (mode === "使用情感描述文本控制" && !String(body.get("emo_text") || "").trim()) {
            return "使用情感描述文本控制时，需要填写情感描述文本。";
        }
        if (mode === "使用情感向量控制") {
            const total = emotionVectorInputs.reduce((sum, input) => sum + Number(input.value || 0), 0);
            if (total <= 0) return "使用情感向量控制时，至少需要设置一个大于 0 的情感向量。";
        }
        return "";
    }

    // 渲染任务信息
    function renderJob(job) {
        if (!job) return;
        stageText.textContent = job.stage || job.status;
        countText.textContent = `${job.progress || 0} / ${job.total || 0}`;
        progressBar.max = Math.max(1, job.total || 1);
        progressBar.value = Math.min(progressBar.max, job.progress || 0);
        currentText.textContent = job.current_text ? `当前句子：${job.current_text}` : "";
        errorText.textContent = job.error || "";
        serviceStatus.textContent = statusLabel(job.status);
        if (pauseButton) pauseButton.disabled = job.status !== "running" || job.pause_requested;
        if (resumeButton) resumeButton.disabled = job.status !== "paused";
        if (cancelButton) cancelButton.disabled = !["queued", "running", "paused"].includes(job.status);
        renderPreview(job.preview);
        renderWarnings(job.warnings || []);

        if (job.download_ready) {
            const url = `/api/jobs/${job.id}/download`;
            downloadLink.href = url;
            downloadLink.hidden = false;
            player.src = url;
            player.hidden = false;
            if (autoPlayWhenDone && autoPlayWhenDone.checked) player.load();
        }
    }

    function renderPreview(data) {
        if (!data || !Array.isArray(data.lines) || data.lines.length === 0) {
            preview.className = "preview empty";
            preview.textContent = "任务开始后显示字幕行。";
            return;
        }
        preview.className = "preview";
        preview.replaceChildren(
            ...data.lines.slice(0, 120).map(line => {
                const row = document.createElement("div");
                row.className = "line-item";
                const time = document.createElement("span");
                time.className = "line-time";
                time.textContent = `${formatMs(line.start_ms)} - ${formatMs(line.end_ms)}`;
                const textSpan = document.createElement("span");
                textSpan.textContent = line.text;
                row.append(time, textSpan);
                return row;
            })
        );
    }

    function renderWarnings(items) {
        warnings.replaceChildren(
            ...[...baseTips, ...items].map(item => {
                const li = document.createElement("li");
                li.textContent = item;
                return li;
            })
        );
    }

    function resetResult() {
        activeJobId = null;
        if (pollTimer) clearInterval(pollTimer);
        pollTimer = null;
        errorText.textContent = "";
        currentText.textContent = "";
        downloadLink.hidden = true;
        downloadLink.removeAttribute("href");
        player.hidden = true;
        player.removeAttribute("src");
        renderWarnings([]);
    }

    async function pollJob() {
        if (!activeJobId) return;
        try {
            const response = await fetch(`/api/jobs/${activeJobId}`);
            const payload = await readJson(response);
            renderJob(payload);
            if (["completed", "failed", "cancelled"].includes(payload.status)) {
                clearInterval(pollTimer);
                pollTimer = null;
                startButton.disabled = false;
                cancelButton.disabled = true;
                pauseButton.disabled = true;
                resumeButton.disabled = true;
            }
        } catch (error) {
            showError(error.message);
        }
    }

    // 表单提交
    if (form) {
        form.addEventListener("submit", async (event) => {
            event.preventDefault();
            resetResult();
            const body = new FormData(form);
            const validationError = validateTtsOptions(body);
            if (validationError) {
                showError(validationError);
                return;
            }
            startButton.disabled = true;
            cancelButton.disabled = false;
            serviceStatus.textContent = "上传中";
            try {
                const response = await fetch("/api/jobs", { method: "POST", body });
                const payload = await readJson(response);
                activeJobId = payload.id;
                serviceStatus.textContent = "处理中";
                renderJob(payload);
                pollTimer = setInterval(pollJob, 1200);
            } catch (error) {
                showError(error.message);
                startButton.disabled = false;
                cancelButton.disabled = true;
            }
        });
    }

    // 取消/暂停/继续
    if (cancelButton) {
        cancelButton.addEventListener("click", async () => {
            if (!activeJobId) return;
            cancelButton.disabled = true;
            await fetch(`/api/jobs/${activeJobId}/cancel`, { method: "POST" });
            await pollJob();
        });
    }
    if (pauseButton) {
        pauseButton.addEventListener("click", async () => {
            if (!activeJobId) return;
            pauseButton.disabled = true;
            await fetch(`/api/jobs/${activeJobId}/pause`, { method: "POST" });
            await pollJob();
        });
    }
    if (resumeButton) {
        resumeButton.addEventListener("click", async () => {
            if (!activeJobId) return;
            resumeButton.disabled = true;
            await fetch(`/api/jobs/${activeJobId}/resume`, { method: "POST" });
            await pollJob();
        });
    }

    // 健康检查 & 版本号
    async function loadHealth() {
        try {
            const response = await fetch("/api/health", { cache: "no-store" });
            const payload = await response.json();
            if (payload.version && appVersionSpan) appVersionSpan.textContent = ` · ${payload.version}`;
        } catch {
            if (appVersionSpan) appVersionSpan.textContent = "";
        }
    }

    // 测试 TTS 连接（通过后端代理，避免跨域）
    async function testTtsConnection() {
        console.log("testTtsConnection triggered");
        if (!ttsUrlInput) {
            console.error("ttsUrlInput not found");
            showToast("未找到 TTS 地址输入框", "error");
            return;
        }
        const url = ttsUrlInput.value.trim();
        if (!url) {
            showToast("请输入 IndexTTS2 地址", "error");
            return;
        }
        if (testTtsUrlBtn) {
            testTtsUrlBtn.disabled = true;
            testTtsUrlBtn.textContent = "测试中...";
        }
        try {
            const response = await fetch("/api/test-tts", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ tts_url: url }),
            });
            const data = await response.json();
            if (data.success) {
                showToast(data.message || "连接成功", "success");
            } else {
                showToast("连接失败: " + (data.error || "未知错误"), "error");
            }
        } catch (err) {
            console.error("Test connection error:", err);
            showToast("请求失败: " + err.message, "error");
        } finally {
            if (testTtsUrlBtn) {
                testTtsUrlBtn.disabled = false;
                testTtsUrlBtn.textContent = "测试连接";
            }
        }
    }

    // 绑定测试按钮事件
    if (testTtsUrlBtn) {
        console.log("Binding test button click");
        testTtsUrlBtn.addEventListener("click", testTtsConnection);
    } else {
        console.warn("testTtsUrlBtn not found in DOM");
    }

    // TTS 地址本地存储
    if (ttsUrlInput) {
        const savedUrl = localStorage.getItem("ttsBaseUrl");
        if (savedUrl) ttsUrlInput.value = savedUrl;
        ttsUrlInput.addEventListener("change", function() {
            const val = this.value.trim();
            if (val) localStorage.setItem("ttsBaseUrl", val);
            else localStorage.removeItem("ttsBaseUrl");
        });
    }

    // 初始化情感控制联动
    if (emoControl) {
        emoControl.addEventListener("change", syncEmotionControls);
        syncEmotionControls();
    }

    loadHealth();
})();