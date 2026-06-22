// ==========================================================================
// 井下压力 HDF5 转换工具前端控制器 - app.js
// ==========================================================================

// 全局应用状态
const state = {
    files: [],            // 扫描到的文件列表: [{path, name, size}]
    fileConfigs: {},      // 每个文件的导出配置: { [filePath]: Config }
    activeTasks: [],      // 当前正在轮询进度的任务 ID 列表
    pollingInterval: null,// 轮询定时器
    currentConfigFilePath: null, // 当前在模态框中编辑的文件路径
    currentFieldItems: [] // 缓存用于极速过滤的字段项 [{element, name, fullPath}]
};

// 页面加载完毕后初始化
document.addEventListener("DOMContentLoaded", () => {
    initApp();
    bindEvents();
});

// 平台无关的提取文件名辅助函数 (支持 / 和 \)
function getFilename(path) {
    if (!path) return "";
    return path.split(/[/\\]/).pop();
}

// 初始化应用
function initApp() {
    // 从 localStorage 恢复历史路径
    const savedSrcPath = localStorage.getItem("h5_src_path");
    const savedOutPath = localStorage.getItem("h5_out_path");
    
    if (savedSrcPath) {
        document.getElementById("input-dir-path").value = savedSrcPath;
        // 页面初始化时也自动扫描已保存的路径
        scanDirectory(savedSrcPath);
    }
    if (savedOutPath) {
        document.getElementById("output-dir-path").value = savedOutPath;
    }
    
    // 初始化批量应用配置标签
    loadBatchPresetsTags();
}

// 绑定所有的事件监听器
function bindEvents() {
    // 监听源路径框变化自动扫描 (失焦或改变时)
    const srcInput = document.getElementById("input-dir-path");
    if (srcInput) {
        srcInput.addEventListener("change", () => {
            const srcPath = srcInput.value.trim();
            if (srcPath) {
                localStorage.setItem("h5_src_path", srcPath);
                scanDirectory(srcPath);
            }
        });

        // 监听回车自动扫描
        srcInput.addEventListener("keydown", (e) => {
            if (e.key === "Enter") {
                const srcPath = srcInput.value.trim();
                if (srcPath) {
                    localStorage.setItem("h5_src_path", srcPath);
                    scanDirectory(srcPath);
                }
            }
        });
    }

    // 监听输出目录输入框的保存
    document.getElementById("output-dir-path").addEventListener("input", (e) => {
        localStorage.setItem("h5_out_path", e.target.value.trim());
    });

    // 文件列表全选/清空
    document.getElementById("btn-select-all").addEventListener("click", () => toggleAllFiles(true));
    document.getElementById("btn-select-none").addEventListener("click", () => toggleAllFiles(false));

    // 批量导出按钮
    document.getElementById("btn-start-batch").addEventListener("click", startBatchExport);

    // 模态框选项卡切换
    const tabButtons = document.querySelectorAll(".tab-btn");
    tabButtons.forEach(btn => {
        btn.addEventListener("click", (e) => {
            const tabId = e.target.getAttribute("data-tab");
            switchTab(tabId, e.target);
        });
    });

    // 模态框预设采样率按钮
    const presetBtns = document.querySelectorAll(".btn-preset");
    presetBtns.forEach(btn => {
        btn.addEventListener("click", (e) => {
            presetBtns.forEach(b => b.classList.remove("active"));
            e.target.classList.add("active");
            
            const val = e.target.getAttribute("data-val");
            document.getElementById("input-interval").value = val;
        });
    });

    // 模态框关闭与取消
    document.getElementById("modal-close-btn").addEventListener("click", closeModal);
    document.getElementById("btn-modal-cancel").addEventListener("click", closeModal);

    // 模态框保存配置
    document.getElementById("btn-modal-save").addEventListener("click", saveModalConfig);

    // 字段选择卡片中的全选/全不选
    document.getElementById("btn-fields-all").addEventListener("click", () => toggleAllFields(true));
    document.getElementById("btn-fields-none").addEventListener("click", () => toggleAllFields(false));

    // 动态侦听时间列的改变
    document.getElementById("select-time-col").addEventListener("change", (e) => {
        handleTimeColChange(e.target.value);
    });

    // 浏览本地源目录和输出目录
    document.getElementById("btn-browse-src").addEventListener("click", () => {
        selectDirectory("input-dir-path", "h5_src_path");
    });
    document.getElementById("btn-browse-out").addEventListener("click", () => {
        selectDirectory("output-dir-path", "h5_out_path");
    });

    // 字段筛选常用配置模板事件
    document.getElementById("select-preset-template").addEventListener("change", (e) => {
        applyPresetTemplate(e.target.value);
    });
    document.getElementById("btn-save-preset").addEventListener("click", saveCurrentAsPreset);
    document.getElementById("btn-delete-preset").addEventListener("click", deleteSelectedPreset);

    // 绑定列表旁边的扫描/刷新按钮事件
    const scanRefreshBtn = document.getElementById("btn-scan-refresh");
    if (scanRefreshBtn) {
        scanRefreshBtn.addEventListener("click", () => {
            const srcInput = document.getElementById("input-dir-path");
            if (srcInput) {
                const srcPath = srcInput.value.trim();
                if (srcPath) {
                    scanDirectory(srcPath);
                } else {
                    showToast("请先在左侧输入源 H5 文件夹或文件路径！", "warning");
                }
            }
        });
    }

    // 字段筛选模糊搜索过滤事件
    const fieldSearchInput = document.getElementById("input-field-search");
    if (fieldSearchInput) {
        fieldSearchInput.addEventListener("input", (e) => {
            let query = e.target.value.toLowerCase().trim();
            
            // 智能检索转译：方便用户进行常规性简称检索（海油 H5 特色匹配）
            query = query.replace(/temperature/g, "temp");
            query = query.replace(/pressure/g, "pres");
            query = query.replace(/fic\s*1/g, "fic s1");
            query = query.replace(/fic\s*2/g, "fic s2");
            query = query.replace(/esp\s*1/g, "esp s1");
            query = query.replace(/esp\s*2/g, "esp s2");
            query = query.replace(/gauge\s*1/g, "gauge s1");
            query = query.replace(/gauge\s*2/g, "gauge s2");
            
            const items = state.currentFieldItems || [];
            for (let i = 0; i < items.length; i++) {
                const item = items[i];
                if (item.name.includes(query) || item.fullPath.includes(query)) {
                    item.element.style.display = "";
                } else {
                    item.element.style.display = "none";
                }
            }
        });
    }

    const clearSearchBtn = document.getElementById("btn-clear-search");
    if (clearSearchBtn) {
        clearSearchBtn.addEventListener("click", () => {
            const input = document.getElementById("input-field-search");
            if (input) input.value = "";
            const items = state.currentFieldItems || [];
            for (let i = 0; i < items.length; i++) {
                items[i].element.style.display = "";
            }
        });
    }
}

// ----------------------------------------------------------------
// 业务逻辑函数
// ----------------------------------------------------------------

// 获取默认选择的字段列表
function getDefaultSelectedFields(datasets, detectedTimeField) {
    const allPaths = datasets.map(d => d.path).filter(p => p !== detectedTimeField);
    
    // 1. 优先搜索 "EQRTZ S1 PRES PSI A" 与 "EQRTZ S1 TEMP CELSIUS A"
    const targetFields = allPaths.filter(p => {
        const lower = p.toLowerCase();
        return lower.endsWith("eqrtz s1 pres psi a") || lower.endsWith("eqrtz s1 temp celsius a") ||
               lower.includes("eqrtz s1 pres psi a") || lower.includes("eqrtz s1 temp celsius a");
    });
    
    if (targetFields.length > 0) {
        return targetFields;
    }
    
    // 2. 如果不存在，搜索包含 pressure/temp/pres/temperature 的字段
    const pressureTempFields = allPaths.filter(p => {
        const lower = p.toLowerCase();
        const isPres = lower.includes("pres") || lower.includes("pressure");
        const isTemp = lower.includes("temp") || lower.includes("temperature");
        return isPres || isTemp;
    });
    
    if (pressureTempFields.length > 0) {
        return pressureTempFields;
    }
    
    // 3. 回退默认：选中所有非时间列的字段
    return allPaths;
}

// 格式化文件大小
function formatBytes(bytes) {
    if (bytes === 0) return '0 B';
    const k = 1024;
    const sizes = ['B', 'KB', 'MB', 'GB'];
    const i = Math.floor(Math.log(bytes) / Math.log(k));
    return parseFloat((bytes / Math.pow(k, i)).toFixed(2)) + ' ' + sizes[i];
}

// 扫描文件夹
async function scanDirectory(path) {
    const btn = document.getElementById("btn-scan-refresh") || document.getElementById("btn-scan");
    if (btn) {
        btn.disabled = true;
        btn.innerText = "🔄 扫描中...";
    }
    
    try {
        const response = await fetch("/api/scan", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ path: path })
        });
        
        if (!response.ok) {
            const err = await response.json();
            throw new Error(err.detail || "扫描失败");
        }
        
        const data = await response.json();
        state.files = data.files || [];
        
        // 自动同步导出保存路径 (若未指定，则默认为扫描源目录)
        const outputInput = document.getElementById("output-dir-path");
        if (!outputInput.value.trim() && state.files.length > 0) {
            let defaultOut = path;
            // 如果输入的是单个文件路径，获取其所在目录
            if (path.toLowerCase().endsWith(".h5") || path.toLowerCase().endsWith(".hdf5")) {
                // 简单的截取目录 (支持 Windows 与 macOS)
                const idx = Math.max(path.lastIndexOf("/"), path.lastIndexOf("\\"));
                if (idx !== -1) {
                    defaultOut = path.substring(0, idx);
                }
            }
            outputInput.value = defaultOut;
            localStorage.setItem("h5_out_path", defaultOut);
        }
        
        renderFileList();
        updateBatchPanelStats();
        loadBatchPresetsTags(); // 重新加载批量应用配置标签
        showToast(`成功扫描到 ${state.files.length} 个 H5 文件`, "success");
        
    } catch (e) {
        showToast(e.message, "error");
    } finally {
        if (btn) {
            btn.disabled = false;
            btn.innerText = "🔄 扫描/刷新";
        }
    }
}

// 渲染表格文件列表
function renderFileList() {
    const tbody = document.getElementById("file-list-body");
    tbody.innerHTML = "";
    
    if (state.files.length === 0) {
        tbody.innerHTML = `
            <tr>
                <td colspan="5" class="empty-state">
                    <div class="empty-icon">📁</div>
                    <p>暂无 H5 文件，请在左侧输入路径并点击扫描</p>
                </td>
            </tr>`;
        return;
    }
    
    state.files.forEach((file, index) => {
        const config = state.fileConfigs[file.path];
        const isConfigured = !!config;
        
        let statusHtml = "";
        let configSummary = "";
        
        if (isConfigured) {
            statusHtml = `<span class="status-pill configured">已配置</span>`;
            
            const fieldCount = config.selectedFields.length;
            const timeDesc = config.timeField ? `${config.interval}S 采样` : "全量(无时间列)";
            configSummary = `<span class="config-summary-text">已选 ${fieldCount} 个字段 | ${timeDesc} | 命名: ${config.customName}</span>`;
        } else {
            statusHtml = `<span class="status-pill unconfigured">默认全量导出</span>`;
            configSummary = `<span class="config-summary-text">未配置，将默认导出所有字段和行数</span>`;
        }
        
        const tr = document.createElement("tr");
        tr.innerHTML = `
            <td>
                <label class="custom-checkbox">
                    <input type="checkbox" class="file-select-checkbox" data-path="${file.path}" checked>
                    <span class="checkmark"></span>
                </label>
            </td>
            <td class="file-name-cell" title="${file.path}">${file.name}</td>
            <td>${formatBytes(file.size)}</td>
            <td>
                ${statusHtml}
                ${configSummary}
            </td>
            <td>
                <div style="display: flex; flex-direction: column; gap: 8px; align-items: flex-start;">
                    <!-- 快速配置标签容器，如果多就横向滚动 -->
                    <div class="row-preset-tags-container" data-path="${file.path}" style="display: flex; gap: 6px; overflow-x: auto; max-width: 260px; padding-bottom: 2px; white-space: nowrap; scrollbar-width: none;">
                        <!-- 动态载入配置标签 -->
                    </div>
                    <button class="btn btn-secondary btn-small btn-config" data-path="${file.path}" style="width: 80px;">手动配置</button>
                </div>
            </td>
        `;
        
        // 绑定复选框改变事件
        tr.querySelector(".file-select-checkbox").addEventListener("change", updateBatchPanelStats);
        
        // 绑定配置按钮事件
        tr.querySelector(".btn-config").addEventListener("click", () => {
            openConfigModal(file.path);
        });
        
        tbody.appendChild(tr);
    });

    // 填充并绑定每行文件自带的快速模板配置标签
    const rowPresetContainers = tbody.querySelectorAll(".row-preset-tags-container");
    const presets = getPresets();
    
    rowPresetContainers.forEach(container => {
        const filePath = container.getAttribute("data-path");
        container.innerHTML = "";
        
        const presetNames = Object.keys(presets);
        if (presetNames.length === 0) {
            container.innerHTML = `<span style="font-size: 11px; color: var(--text-muted); line-height: 22px;">暂无常用配置</span>`;
            return;
        }
        
        presetNames.forEach(name => {
            const badge = document.createElement("span");
            badge.className = "preset-badge";
            badge.innerText = name;
            badge.style.cssText = `
                display: inline-block;
                padding: 3px 8px;
                font-size: 11px;
                border-radius: 12px;
                background-color: rgba(255, 255, 255, 0.05);
                border: 1px solid var(--border-color);
                color: var(--text-muted);
                cursor: pointer;
                transition: var(--transition);
                user-select: none;
            `;
            
            // Hover 效果
            badge.addEventListener("mouseenter", () => {
                badge.style.backgroundColor = "rgba(238, 127, 34, 0.15)";
                badge.style.borderColor = "var(--orange-accent, #EE7F22)";
                badge.style.color = "#fff";
            });
            badge.addEventListener("mouseleave", () => {
                badge.style.backgroundColor = "rgba(255, 255, 255, 0.05)";
                badge.style.borderColor = "var(--border-color)";
                badge.style.color = "var(--text-muted)";
            });
            
            badge.addEventListener("click", async () => {
                badge.style.opacity = "0.5";
                await applyTemplateToFile(filePath, name);
                badge.style.opacity = "1";
                renderFileList(); // 刷新状态列展示
                updateBatchPanelStats();
                showToast(`已成功为该文件应用常用配置 "${name}"`, "success");
            });
            
            container.appendChild(badge);
        });
    });
}

// 全选或清空文件选择
function toggleAllFiles(checked) {
    const checkboxes = document.querySelectorAll(".file-select-checkbox");
    checkboxes.forEach(cb => cb.checked = checked);
    updateBatchPanelStats();
}

// 更新统计数据并激活/禁用批量导出按钮
function updateBatchPanelStats() {
    const total = state.files.length;
    const checkboxes = document.querySelectorAll(".file-select-checkbox");
    let selectedCount = 0;
    checkboxes.forEach(cb => {
        if (cb.checked) selectedCount++;
    });
    
    document.getElementById("stat-total-files").innerText = total;
    document.getElementById("stat-selected-files").innerText = selectedCount;
    
    const startBtn = document.getElementById("btn-start-batch");
    startBtn.disabled = selectedCount === 0;
}

// ----------------------------------------------------------------
// 模态框配置交互
// ----------------------------------------------------------------

// 选项卡切换
function switchTab(tabId, targetBtn) {
    // 切换按钮状态
    const tabBtns = document.querySelectorAll(".tab-btn");
    tabBtns.forEach(btn => btn.classList.remove("active"));
    targetBtn.classList.add("active");
    
    // 切换面板展示
    const panes = document.querySelectorAll(".tab-pane");
    panes.forEach(pane => pane.classList.remove("active"));
    document.getElementById(tabId).classList.add("active");
}

// 全选/不选字段列表
function toggleAllFields(checked) {
    const checkboxes = document.querySelectorAll(".field-checkbox");
    checkboxes.forEach(cb => cb.checked = checked);
}

// 打开模态框，按需异步解析文件元数据
async function openConfigModal(filePath) {
    state.currentConfigFilePath = filePath;
    const filename = getFilename(filePath);
    document.getElementById("modal-filename").innerText = filename;
    
    // 初始化 Tab 显示为第一页
    switchTab("tab-fields", document.querySelector('[data-tab="tab-fields"]'));
    
    // 判断是否已经解析过此文件
    let config = state.fileConfigs[filePath];
    
    // 显示 Loading 状态
    const fieldsContainer = document.getElementById("fields-container");
    fieldsContainer.innerHTML = `<p style="grid-column: 1/-1; text-align: center; color: var(--text-muted); padding: 40px;">正在深度读取 HDF5 文件结构，请稍候...</p>`;
    
    // 锁定模态框操作
    document.getElementById("btn-modal-save").disabled = true;
    document.getElementById("config-modal").style.display = "flex";
    
    try {
        let inspectData = null;
        
        if (config && config.isInspected) {
            // 已缓存
            inspectData = config;
        } else {
            // 未缓存，向后端请求解析
            const response = await fetch("/api/inspect", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path: filePath })
            });
            
            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || "读取文件元数据失败");
            }
            
            inspectData = await response.json();
            inspectData.isInspected = true;
        }
        
        // 缓存解析结果 (在渲染字段列表前初始化，确保默认字段勾选正常生效)
        if (!config) {
            config = {
                filePath: filePath,
                isInspected: true,
                datasets: inspectData.datasets,
                detectedTimeField: inspectData.detectedTimeField,
                timeType: inspectData.timeType,
                originalMinTime: inspectData.timeMinStr,
                originalMaxTime: inspectData.timeMaxStr,
                // 下面为初始化的用户设置值
                selectedFields: getDefaultSelectedFields(inspectData.datasets, inspectData.detectedTimeField),
                timeField: inspectData.detectedTimeField,
                timeTypeSelected: inspectData.timeType,
                startTimeStr: inspectData.timeMinStr,
                endTimeStr: inspectData.timeMaxStr,
                baseDate: "1970-01-01 00:00:00",
                interval: 10.0, // 默认重采样间隔 10S
                customName: filename.substring(0, filename.lastIndexOf(".")) + "_export.xlsx",
                tempUnit: "degC",
                presUnit: "PSI"
            };
            state.fileConfigs[filePath] = config;
        }

        // 载入常用配置模板
        loadTemplates();

        // 渲染字段列表
        renderFields(inspectData.datasets, config.selectedFields);
        
        // 重置并清空字段筛选文本框
        const searchInput = document.getElementById("input-field-search");
        if (searchInput) {
            searchInput.value = "";
            // 绑定筛选事件
            searchInput.oninput = (e) => {
                const val = e.target.value.toLowerCase();
                state.currentFieldItems.forEach(item => {
                    const match = item.name.includes(val) || item.fullPath.includes(val);
                    item.element.style.display = match ? "flex" : "none";
                });
            };
        }
        
        // 渲染时间与重采样页面
        renderTimeSettings(inspectData, config);
        
        // 渲染输出命名页面
        document.getElementById("input-custom-name").value = config.customName;
        
        document.getElementById("btn-modal-save").disabled = false;
        
    } catch(e) {
        showToast(e.message, "error");
        closeModal();
    }
}

// 关闭模态框
function closeModal() {
    document.getElementById("config-modal").style.display = "none";
    state.currentConfigFilePath = null;
}

// 渲染字段 Checkbox 列表
function renderFields(datasets, selectedFields) {
    const container = document.getElementById("fields-container");
    container.innerHTML = "";
    state.currentFieldItems = []; // 清空缓存
    
    if (!datasets || datasets.length === 0) {
        container.innerHTML = `<p style="grid-column: 1/-1; text-align: center; color: var(--text-muted);">未在文件中发现有效的一维数据集</p>`;
        return;
    }
    
    datasets.forEach(ds => {
        // 如果是已被缓存的选择状态，按缓存来；否则默认全部勾选
        const isChecked = selectedFields ? selectedFields.includes(ds.path) : true;
        
        const div = document.createElement("div");
        div.className = "field-item";
        div.innerHTML = `
            <label class="custom-checkbox">
                <input type="checkbox" class="field-checkbox" value="${ds.path}" ${isChecked ? 'checked' : ''}>
                <span class="checkmark"></span>
            </label>
            <div class="field-details">
                <span class="field-name" title="${ds.path}">${getFilename(ds.path)}</span>
                <span class="field-meta">大小: ${ds.size} 行 | 类型: ${ds.dtype}</span>
            </div>
        `;
        container.appendChild(div);
        
        // Cache DOM nodes and precomputed names for 60fps fuzzy filtering
        state.currentFieldItems.push({
            element: div,
            name: getFilename(ds.path).toLowerCase(),
            fullPath: ds.path.toLowerCase()
        });
    });
}

// 渲染时间与重采样相关的 UI 元素
function renderTimeSettings(inspectData, config) {
    const timeDropdown = document.getElementById("select-time-col");
    timeDropdown.innerHTML = '<option value="">无时间列 (仅按物理行导出)</option>';
    
    inspectData.datasets.forEach(ds => {
        const opt = document.createElement("option");
        opt.value = ds.path;
        opt.innerText = getFilename(ds.path);
        timeDropdown.appendChild(opt);
    });
    
    // 设置已选的时间列
    const currentTimeCol = config ? config.timeField : inspectData.detectedTimeField;
    timeDropdown.value = currentTimeCol || "";
    
    // 时间类型展示
    const timeTypeSelected = config ? config.timeTypeSelected : inspectData.timeType;
    document.getElementById("select-time-type").value = timeTypeSelected || "timestamp_seconds";
    
    // 时间跨度展示
    document.getElementById("txt-file-time-span").innerText = inspectData.timeMinStr && inspectData.timeMaxStr 
        ? `${inspectData.timeMinStr} 至 ${inspectData.timeMaxStr}` 
        : "无绝对时间";
        
    // 默认加载起止时间
    document.getElementById("input-start-time").value = config ? config.startTimeStr : (inspectData.timeMinStr || "");
    document.getElementById("input-end-time").value = config ? config.endTimeStr : (inspectData.timeMaxStr || "");
    
    // 基准时间
    document.getElementById("input-base-date").value = config ? config.baseDate : "1970-01-01 00:00:00";
    
    // 间隔与预设激活
    const currentInterval = config ? config.interval : 10.0;
    document.getElementById("input-interval").value = currentInterval;
    
    const presets = document.querySelectorAll(".btn-preset");
    presets.forEach(p => {
        const val = parseFloat(p.getAttribute("data-val"));
        if (val === currentInterval) {
            p.classList.add("active");
        } else {
            p.classList.remove("active");
        }
    });
    
    // 设置已选的温压单位
    document.getElementById("select-temp-unit").value = config ? (config.tempUnit || "degC") : "degC";
    document.getElementById("select-pres-unit").value = config ? (config.presUnit || "PSI") : "PSI";
    
    // 处理特定配置的显示/隐藏
    handleTimeColChange(currentTimeCol, timeTypeSelected);
}

// 处理时间列改变后的 UI 调整
function handleTimeColChange(timeColPath, forceType = null) {
    const isTimeSelected = !!timeColPath;
    const timeRangeGroup = document.getElementById("time-range-container");
    const baseDateGroup = document.getElementById("group-base-date");
    const intervalHelp = document.getElementById("interval-help-text");
    
    if (isTimeSelected) {
        timeRangeGroup.style.display = "block";
        intervalHelp.innerText = "默认 1.0 秒。0 表示保留全部原始采样点。";
        
        // 自动判定类型
        let type = forceType;
        if (!type && state.currentConfigFilePath) {
            const cache = state.fileConfigs[state.currentConfigFilePath];
            if (cache && cache.datasets) {
                // 如果改变了时间列，重新做一次推导（实际上一般只有一列时间，这里做基础防护）
                const name = getFilename(timeColPath).toLowerCase();
                // 默认秒级
                type = "timestamp_seconds";
                if (name.includes("ms") || name.includes("millisecond")) {
                    type = "timestamp_ms";
                } else if (name.includes("elapsed") || name.includes("relative") || name.includes("sec") || name === "t") {
                    type = "relative_seconds";
                }
            }
        }
        
        document.getElementById("select-time-type").value = type || "timestamp_seconds";
        
        if (type === "relative_seconds") {
            baseDateGroup.style.display = "block";
        } else {
            baseDateGroup.style.display = "none";
        }
    } else {
        // 无时间列
        timeRangeGroup.style.display = "none";
        baseDateGroup.style.display = "none";
        intervalHelp.innerText = "没有选择时间列。此处数值表示每 N 行抽取 1 行（例如：输入 10 表示每 10 行提取 1 行数据，输入 0 或 1 表示导出所有物理行）。";
    }
}

// 保存模态框配置
function saveModalConfig() {
    const filePath = state.currentConfigFilePath;
    if (!filePath) return;
    
    // 1. 获取选中的字段
    const fieldCheckboxes = document.querySelectorAll(".field-checkbox:checked");
    const selectedFields = Array.from(fieldCheckboxes).map(cb => cb.value);
    
    if (selectedFields.length === 0) {
        showToast("请至少勾选一个需要导出的字段！", "warning");
        return;
    }
    
    // 2. 时间与采样参数
    const timeField = document.getElementById("select-time-col").value;
    const timeTypeSelected = document.getElementById("select-time-type").value;
    const baseDate = document.getElementById("input-base-date").value.trim();
    const startTimeStr = document.getElementById("input-start-time").value.trim();
    const endTimeStr = document.getElementById("input-end-time").value.trim();
    
    let interval = parseFloat(document.getElementById("input-interval").value);
    if (isNaN(interval) || interval < 0) {
        interval = 1.0;
    }
    
    // 2.5 获取温压单位
    const tempUnit = document.getElementById("select-temp-unit").value;
    const presUnit = document.getElementById("select-pres-unit").value;
    
    // 3. 输出名称
    const customName = document.getElementById("input-custom-name").value.trim();
    if (!customName) {
        showToast("输出文件名不能为空", "warning");
        return;
    }
    
    // 更新或保存到缓存
    state.fileConfigs[filePath] = {
        ...state.fileConfigs[filePath],
        selectedFields,
        timeField,
        timeTypeSelected,
        startTimeStr,
        endTimeStr,
        baseDate,
        interval,
        customName,
        tempUnit,
        presUnit
    };
    
    // 刷新列表表格以展示已配置状态
    renderFileList();
    updateBatchPanelStats();
    showToast("配置保存成功", "success");
    closeModal();
}

// ----------------------------------------------------------------
// 批量导出多线程执行
// ----------------------------------------------------------------

// 异步顺序解析“被勾选但从未配置过”的文件，生成默认参数后再批量导出
async function startBatchExport() {
    const checkboxes = document.querySelectorAll(".file-select-checkbox:checked");
    const selectedPaths = Array.from(checkboxes).map(cb => cb.getAttribute("data-path"));
    
    if (selectedPaths.length === 0) {
        showToast("请勾选需要导出的 H5 文件！", "warning");
        return;
    }
    
    const outputDir = document.getElementById("output-dir-path").value.trim();
    if (!outputDir) {
        showToast("请指定导出的保存目录！", "warning");
        return;
    }
    
    const startBtn = document.getElementById("btn-start-batch");
    startBtn.disabled = true;
    startBtn.innerHTML = `<span>⏳ 正在自动解析未配置的文件...</span>`;
    
    try {
        const finalConfigs = [];
        
        // 自动提取每一个被选中文件元数据（若之前没点击配置按钮，则在此做静默解析）
        for (let i = 0; i < selectedPaths.length; i++) {
            const path = selectedPaths[i];
            let config = state.fileConfigs[path];
            
            if (!config) {
                // 自动进行 inspect
                startBtn.innerHTML = `<span>⏳ 正在解析 [${i+1}/${selectedPaths.length}] 的元数据...</span>`;
                
                const response = await fetch("/api/inspect", {
                    method: "POST",
                    headers: { "Content-Type": "application/json" },
                    body: JSON.stringify({ path: path })
                });
                
                if (!response.ok) {
                    throw new Error(`解析文件失败: ${path}`);
                }
                
                const inspectData = await response.json();
                const filename = getFilename(path);
                
                 // 生成一键默认导出配置：导出除时间外的所有字段，默认 10S 采样，最大范围
                 config = {
                     filePath: path,
                     selectedFields: getDefaultSelectedFields(inspectData.datasets, inspectData.detectedTimeField),
                     timeField: inspectData.detectedTimeField,
                     timeTypeSelected: inspectData.timeType,
                     startTimeStr: inspectData.timeMinStr,
                     endTimeStr: inspectData.timeMaxStr,
                     baseDate: "1970-01-01 00:00:00",
                     interval: 10.0,
                     customName: filename.substring(0, filename.lastIndexOf(".")) + "_export.xlsx",
                     tempUnit: "degC",
                     presUnit: "PSI"
                 };
                 
                 state.fileConfigs[path] = config;
             }
             
             finalConfigs.push({
                 filePath: config.filePath,
                 selectedFields: config.selectedFields,
                 timeField: config.timeField || null,
                 timeType: config.timeTypeSelected || null,
                 startTimeStr: config.startTimeStr || null,
                 endTimeStr: config.endTimeStr || null,
                 baseDate: config.baseDate || "1970-01-01 00:00:00",
                 interval: config.interval,
                 customName: config.customName,
                 tempUnit: config.tempUnit || "degC",
                 presUnit: config.presUnit || "PSI"
             });
         }
        
        // 发送导出请求给后端
        startBtn.innerHTML = `<span>⏳ 正在提交导出任务...</span>`;
        const exportResponse = await fetch("/api/export", {
            method: "POST",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({
                configs: finalConfigs,
                outputDir: outputDir
            })
        });
        
        if (!exportResponse.ok) {
            const err = await exportResponse.json();
            throw new Error(err.detail || "提交任务失败");
        }
        
        const exportResult = await exportResponse.json();
        const taskIds = exportResult.taskIds; // 后端分配的 UUID 列表
        
        // 渲染进度卡片
        showProgressPanel(taskIds, finalConfigs);
        
        // 开启定时器轮询进度
        startPolling(taskIds);
        
    } catch(e) {
        showToast(e.message, "error");
        startBtn.disabled = false;
        startBtn.innerText = "🚀 开始多线程批量导出";
    }
}

// 展开进度条容器，初始化卡片
function showProgressPanel(taskIds, configs) {
    const container = document.getElementById("progress-container");
    const list = document.getElementById("progress-list");
    list.innerHTML = "";
    
    document.getElementById("progress-summary").innerText = "正在多线程处理中...";
    document.getElementById("progress-summary").className = "badge badge-pulse";
    
    configs.forEach((cfg, index) => {
        const taskId = taskIds[index];
        const filename = getFilename(cfg.filePath);
        
        const item = document.createElement("div");
        item.className = "progress-item";
        item.id = `task-card-${taskId}`;
        item.innerHTML = `
            <div class="progress-header">
                <span class="progress-title" title="${cfg.filePath}">📄 ${filename}</span>
                <span class="progress-percent" id="task-pct-${taskId}">0%</span>
            </div>
            <div class="progress-bar-bg">
                <div class="progress-bar-fill" id="task-bar-${taskId}" style="width: 0%;"></div>
            </div>
            <div class="progress-status-msg">
                <span id="task-msg-${taskId}">排队中...</span>
                <span id="task-action-${taskId}"></span>
            </div>
        `;
        list.appendChild(item);
    });
    
    container.style.display = "block";
    container.scrollIntoView({ behavior: 'smooth' });
}

// 开始进度轮询
function startPolling(taskIds) {
    state.activeTasks = [...taskIds];
    
    if (state.pollingInterval) {
        clearInterval(state.pollingInterval);
    }
    
    state.pollingInterval = setInterval(async () => {
        if (state.activeTasks.length === 0) {
            clearInterval(state.pollingInterval);
            state.pollingInterval = null;
            
            // 恢复批量按钮
            const startBtn = document.getElementById("btn-start-batch");
            startBtn.disabled = false;
            startBtn.innerText = "🚀 开始多线程批量导出";
            
            document.getElementById("progress-summary").innerText = "批量处理完毕";
            document.getElementById("progress-summary").className = "badge";
            
            showToast("所有勾选的 H5 文件导出任务已处理完毕！", "success");
            return;
        }
        
        try {
            // 批量查询状态
            const response = await fetch(`/api/status?taskIds=${state.activeTasks.join(",")}`);
            if (!response.ok) return;
            
            const statusData = await response.json();
            
            state.activeTasks.forEach((taskId, idx) => {
                const info = statusData[taskId];
                if (!info) return;
                
                // 更新页面对应卡片的进度条与文本
                const bar = document.getElementById(`task-bar-${taskId}`);
                const pct = document.getElementById(`task-pct-${taskId}`);
                const msg = document.getElementById(`task-msg-${taskId}`);
                const act = document.getElementById(`task-action-${taskId}`);
                
                if (bar) bar.style.width = `${info.progress}%`;
                if (pct) pct.innerText = `${info.progress}%`;
                if (msg) msg.innerText = info.message;
                
                // 处理任务终止状态
                if (info.status === "completed") {
                    if (bar) bar.classList.add("completed");
                    // 显示复制文件路径的辅助操作
                    if (act) {
                        act.innerHTML = `<button class="btn-open-dir" onclick="navigator.clipboard.writeText('${info.output_path.replace(/\\/g, '\\\\')}'); alert('已成功复制导出路径！');">📋 复制路径</button>`;
                    }
                    // 从轮询列表中移除该任务
                    state.activeTasks = state.activeTasks.filter(id => id !== taskId);
                } else if (info.status === "failed") {
                    if (bar) {
                        bar.classList.add("failed");
                        bar.style.width = "100%";
                    }
                    if (pct) pct.innerText = "❌ 失败";
                    if (msg) msg.innerText = info.message;
                    // 从轮询列表中移除该任务
                    state.activeTasks = state.activeTasks.filter(id => id !== taskId);
                }
            });
            
        } catch(e) {
            console.error("轮询进度出错:", e);
        }
    }, 600);
}

// ----------------------------------------------------------------
// 文件夹选择与常用配置模版管理
// ----------------------------------------------------------------

// 异步调起系统原生文件夹选择器
async function selectDirectory(targetInputId, storageKey) {
    try {
        const response = await fetch("/api/browse", {
            method: "POST",
            headers: { "Content-Type": "application/json" }
        });
        
        if (!response.ok) {
            throw new Error("调起文件夹选择器失败");
        }
        
        const data = await response.json();
        if (data.path) {
            document.getElementById(targetInputId).value = data.path;
            localStorage.setItem(storageKey, data.path);
            showToast(`已成功选择路径: ${data.path}`, "success");
            
            // 如果是源文件夹改变，且导出文件夹为空，自动同步
            if (targetInputId === "input-dir-path") {
                const outInput = document.getElementById("output-dir-path");
                if (!outInput.value.trim()) {
                    outInput.value = data.path;
                    localStorage.setItem("h5_out_path", data.path);
                }
                // 浏览目录后自动扫描
                scanDirectory(data.path);
            }
        } else if (data.error) {
            showToast(`调起系统文件夹选择器失败: ${data.error}`, "error");
        }
    } catch (e) {
        showToast("无法打开本地文件夹选择器，请手动输入路径。", "warning");
    }
}

// 获取所有常用配置
function getPresets() {
    const data = localStorage.getItem("h5_field_presets");
    return data ? JSON.parse(data) : {};
}

// 保存常用配置
function savePresets(presets) {
    localStorage.setItem("h5_field_presets", JSON.stringify(presets));
}

// 载入并渲染常用配置模板下拉菜单与批量应用下拉框
function loadTemplates() {
    const select = document.getElementById("select-preset-template");
    const deleteBtn = document.getElementById("btn-delete-preset");
    
    select.innerHTML = '<option value="">-- 应用常用字段配置 --</option>';
    
    const presets = getPresets();
    Object.keys(presets).forEach(name => {
        const opt = document.createElement("option");
        opt.value = name;
        opt.innerText = name;
        select.appendChild(opt);
    });
    
    deleteBtn.style.display = "none";
    
    // 同时更新主界面的批量标签
    loadBatchPresetsTags();
}

// 载入并渲染一级界面的全局批量应用常用配置标签
function loadBatchPresetsTags() {
    const container = document.getElementById("batch-preset-tags-container");
    if (!container) return;
    
    container.innerHTML = "";
    
    const presets = getPresets();
    const presetNames = Object.keys(presets);
    
    if (presetNames.length === 0) {
        container.innerHTML = '<span style="color: var(--text-muted); font-size: 11px;">(暂无常用配置)</span>';
        return;
    }
    
    // 添加说明标签
    const label = document.createElement("span");
    label.style.fontSize = "11px";
    label.style.color = "var(--text-muted)";
    label.style.marginRight = "6px";
    label.innerText = "批量应用:";
    container.appendChild(label);
    
    presetNames.forEach(name => {
        const badge = document.createElement("span");
        badge.className = "preset-badge";
        badge.innerText = name;
        
        // 样式适配 CNOOC 风格，和行内样式统一
        badge.style.cssText = `
            display: inline-block;
            padding: 3px 8px;
            font-size: 11px;
            border-radius: 12px;
            background-color: rgba(0, 122, 255, 0.1);
            border: 1px solid rgba(0, 122, 255, 0.25);
            color: var(--primary, #007aff);
            cursor: pointer;
            transition: var(--transition, all 0.2s ease);
            user-select: none;
            font-weight: 500;
        `;
        
        // Hover 效果
        badge.addEventListener("mouseenter", () => {
            badge.style.backgroundColor = "rgba(238, 127, 34, 0.15)";
            badge.style.borderColor = "var(--orange-accent, #EE7F22)";
            badge.style.color = "#fff";
            badge.style.transform = "translateY(-1px)";
        });
        badge.addEventListener("mouseleave", () => {
            badge.style.backgroundColor = "rgba(0, 122, 255, 0.1)";
            badge.style.borderColor = "rgba(0, 122, 255, 0.25)";
            badge.style.color = "var(--primary, #007aff)";
            badge.style.transform = "none";
        });
        
        // 点击批量应用该配置模板
        badge.addEventListener("click", async () => {
            const checkboxes = document.querySelectorAll(".file-select-checkbox:checked");
            const selectedPaths = Array.from(checkboxes).map(cb => cb.getAttribute("data-path"));
            
            if (selectedPaths.length === 0) {
                showToast("请先在列表左侧勾选您需要应用模板的 H5 文件！", "warning");
                return;
            }
            
            // 提示用户正在应用
            showToast(`正在批量应用常用配置 "${name}"...`, "info");
            
            // 禁用所有标签，避免重复点击
            const badges = container.querySelectorAll(".preset-badge");
            badges.forEach(b => { b.style.pointerEvents = "none"; b.style.opacity = "0.5"; });
            
            let successCount = 0;
            for (let path of selectedPaths) {
                await applyTemplateToFile(path, name);
                successCount++;
            }
            
            // 重新启用标签
            badges.forEach(b => { b.style.pointerEvents = "auto"; b.style.opacity = "1"; });
            
            // 刷新文件列表显示状态
            renderFileList();
            updateBatchPanelStats();
            
            showToast(`成功将配置 "${name}" 批量应用至 ${successCount} 个文件`, "success");
        });
        
        container.appendChild(badge);
    });
}

// 针对单个 H5 文件静默应用模板配置并缓存其字段勾选状态
async function applyTemplateToFile(filePath, templateName) {
    const presets = getPresets();
    const templateFields = presets[templateName];
    if (!templateFields) return;
    
    let config = state.fileConfigs[filePath];
    let inspectData = null;
    const filename = getFilename(filePath);
    
    try {
        if (config && config.isInspected) {
            inspectData = config;
        } else {
            // 静默读取 H5 文件的元数据字段
            const response = await fetch("/api/inspect", {
                method: "POST",
                headers: { "Content-Type": "application/json" },
                body: JSON.stringify({ path: filePath })
            });
            
            if (!response.ok) {
                const err = await response.json();
                throw new Error(err.detail || "读取文件元数据失败");
            }
            
            inspectData = await response.json();
            inspectData.isInspected = true;
        }
        
        // 匹配字段列表
        const selectedFields = [];
        inspectData.datasets.forEach(ds => {
            const path = ds.path;
            const baseName = getFilename(path);
            // 完整路径或字段名存在于模板中
            if (templateFields.includes(path) || templateFields.includes(baseName)) {
                selectedFields.push(path);
            }
        });
        
        if (selectedFields.length === 0) {
            showToast(`模板 "${templateName}" 无法与文件 "${filename}" 的任何字段相匹配`, "warning");
            return;
        }
        
        state.fileConfigs[filePath] = {
            filePath: filePath,
            isInspected: true,
            datasets: inspectData.datasets,
            detectedTimeField: inspectData.detectedTimeField,
            timeType: inspectData.timeType,
            originalMinTime: inspectData.timeMinStr,
            originalMaxTime: inspectData.timeMaxStr,
            // 用户设置值
            selectedFields: selectedFields,
            timeField: inspectData.detectedTimeField,
            timeTypeSelected: inspectData.timeType,
            startTimeStr: inspectData.timeMinStr,
            endTimeStr: inspectData.timeMaxStr,
            baseDate: "1970-01-01 00:00:00",
            interval: 10.0, // 默认 10S
            customName: filename.substring(0, filename.lastIndexOf(".")) + "_export.xlsx"
        };
        
        showToast(`已为 "${filename}" 应用配置模板: ${templateName}`, "success");
    } catch (e) {
        showToast(`静默解析 [${filename}] 结构失败: ${e.message}`, "error");
    }
}

// 批量将模板应用给所有已勾选的文件 (已废弃，直接由标签点击事件处理)

// 应用选中的配置模板
function applyPresetTemplate(name) {
    if (!name) {
        document.getElementById("btn-delete-preset").style.display = "none";
        return;
    }
    
    const presets = getPresets();
    const templateFields = presets[name];
    if (!templateFields) return;
    
    // 显示删除按钮
    document.getElementById("btn-delete-preset").style.display = "inline-flex";
    
    const checkboxes = document.querySelectorAll(".field-checkbox");
    let count = 0;
    
    checkboxes.forEach(cb => {
        const path = cb.value;
        const baseName = path.split("/").pop();
        
        // 匹配字段路径完整匹配，或者尾部叶子节点名称匹配
        if (templateFields.includes(path) || templateFields.includes(baseName)) {
            cb.checked = true;
            count++;
        } else {
            cb.checked = false;
        }
    });
    
    showToast(`成功应用配置 "${name}"，自动匹配勾选了 ${count} 个字段。`, "success");
}

// 保存当前所勾选的字段为新模板
function saveCurrentAsPreset() {
    const checkedBoxes = document.querySelectorAll(".field-checkbox:checked");
    if (checkedBoxes.length === 0) {
        showToast("当前没有勾选任何字段，无法保存模板！", "warning");
        return;
    }
    
    const name = prompt("请输入此常用字段配置模板的名称 (例如: 压力与温度)：");
    if (name === null) return; // 取消
    
    const cleanName = name.trim();
    if (!cleanName) {
        showToast("模板名称不能为空！", "warning");
        return;
    }
    
    // 存储字段的叶子节点名称 (baseName)，这样能自适应不同 schema 命名的 H5 文件
    const fieldNames = Array.from(checkedBoxes).map(cb => cb.value.split("/").pop());
    
    const presets = getPresets();
    presets[cleanName] = fieldNames;
    savePresets(presets);
    
    loadTemplates();
    document.getElementById("select-preset-template").value = cleanName;
    document.getElementById("btn-delete-preset").style.display = "inline-flex";
    
    showToast(`配置模板 "${cleanName}" 已保存！`, "success");
}

// 删除当前选中的模板
function deleteSelectedPreset() {
    const select = document.getElementById("select-preset-template");
    const name = select.value;
    if (!name) return;
    
    if (confirm(`是否确定删除常用配置模板 "${name}"？`)) {
        const presets = getPresets();
        delete presets[name];
        savePresets(presets);
        
        loadTemplates();
        showToast("模板已成功删除", "success");
    }
}

// ----------------------------------------------------------------
// 辅助通知 Toast 系统
// ----------------------------------------------------------------

function showToast(message, type = "success") {
    const container = document.getElementById("toast-container");
    const toast = document.createElement("div");
    toast.className = `toast ${type}`;
    
    let icon = "💡";
    if (type === "success") icon = "✅";
    if (type === "error") icon = "❌";
    if (type === "warning") icon = "⚠️";
    
    toast.innerHTML = `<span>${icon}</span> <span>${message}</span>`;
    container.appendChild(toast);
    
    // 3 秒后淡出并销毁
    setTimeout(() => {
        toast.style.opacity = "0";
        toast.style.transform = "translateY(-10px)";
        setTimeout(() => {
            container.removeChild(toast);
        }, 300);
    }, 3500);
}
