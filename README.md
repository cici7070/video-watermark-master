# 影片去浮水印大師

這是一個在 Windows 電腦本機處理的影片工具，協助你移除影片中「固定位置」的 Logo 或浮水印。影片不會上傳到外部服務；請只處理自己製作、已購買或已取得授權的影片。

## 這個版本可以做什麼

- 支援 60 秒內的 MP4、MOV、AVI 影片。
- 協助找出固定位置的 Logo，並讓你確認、調整、刪除或新增處理範圍。
- 可選擇快速模式或高品質模式，輸出為 MP4。
- 盡量保留原始解析度、比例與音訊。

目前不支援「會移動」的浮水印。自動偵測也可能把固定字幕誤認成 Logo，所以開始處理前，請先確認畫面上的範圍，只保留真正需要移除的內容。

## 第一次安裝

### 1. 安裝 Python 3.11（Windows 64 位元）

請安裝 **Python 3.11 的 Windows 64 位元版本**。安裝時請勾選「Add Python to PATH」。安裝完成後，重新開啟 PowerShell，輸入：

```powershell
py -3.11 --version
```

若看到 `Python 3.11.x`，代表 Python 已準備完成。

### 2. 安裝 FFmpeg 並加入 PATH

請先到 [FFmpeg 官方下載頁](https://ffmpeg.org/download.html) 選擇 Windows build。官方頁會連到 `gyan.dev`、BtbN 等 Windows build 提供者；不需要指定或追求某一個特定版本，選擇適合 Windows 的一般穩定 build 即可。

第一次安裝可照以下短流程完成：

1. 在 Windows build 下載頁下載 **Windows essentials ZIP** 壓縮檔。
2. 解壓縮到你容易找到的位置，例如 `C:\Tools\ffmpeg`，再打開解壓縮後的資料夾，找到其中的 `bin` 資料夾。
3. 在 Windows 搜尋「系統內容」，依序開啟「進階系統設定」→「環境變數」→ 選取 `Path` →「編輯」→「新增」，貼上 `bin` 的**完整路徑**（例如 `C:\Tools\ffmpeg\bin`）。接著一路按「確定」儲存。
4. 關閉所有 PowerShell 視窗，重新開啟 PowerShell，讓新的 Path 設定生效。

完成後依序確認：

```powershell
ffmpeg -version
ffprobe -version
```

兩個指令都必須顯示版本資訊。請不要把 FFmpeg 的 `.exe` 二進位檔複製或提交到本專案；它應安裝在你的系統中並透過 PATH 使用。

### 3. 建立專案環境與安裝套件

在本專案資料夾的空白處按右鍵，選擇「在終端機中開啟」或開啟 PowerShell 後切換到此資料夾，依序執行：

```powershell
py -3.11 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

### 4. 選配：高品質 LaMa 模式

快速模式是 MVP 推薦的預設選擇，安裝完成即可使用。若你想嘗試 LaMa 高品質模式，再執行：

```powershell
.\.venv\Scripts\python.exe -m pip install -r requirements-ai.txt
```

LaMa 第一次使用時會下載模型；只有套件與模型下載會連線，**你的影片不會被上傳**。此高品質套件較舊，若與電腦環境不相容，不會影響快速模式的上傳、偵測與處理。

## 啟動工具

最簡單的方法是直接雙擊 [start.bat](start.bat)。它會先檢查 Python 環境、ffmpeg 與 ffprobe，沒有準備好時會提示你回來查看本說明。

也可以在 PowerShell 執行：

```powershell
.\.venv\Scripts\python.exe -m streamlit run app.py
```

終端機會顯示本機網址，通常會自動在瀏覽器開啟；沒有自動開啟時，複製網址貼到瀏覽器即可。

## 使用方式：6 步完成一支影片

1. 開啟工具後，上傳一支 60 秒內的 MP4、MOV 或 AVI。
2. 按「開始偵測浮水印」。
3. 確認預覽中的候選範圍，只保留真正要移除的固定 Logo。
4. 若沒有找到 Logo，按「新增範圍」，輸入位置與大小；若偵測錯誤可刪除或調整範圍。
5. 優先選擇「快速模式」；需要時才改選「高品質模式」。
6. 按「開始處理」，完成後預覽結果並下載 MP4。

## 開發與測試指令

若你要確認工具是否正常，可在 PowerShell 執行：

```powershell
.\.venv\Scripts\python.exe -m pytest -v
.\.venv\Scripts\python.exe -m pip check
.\.venv\Scripts\python.exe -m compileall app.py src tests
```

## 常見問題／故障排除

### 找不到 ffmpeg 或 ffprobe

代表 FFmpeg 尚未安裝，或安裝後沒有重新開啟 PowerShell。請先重新執行 `ffmpeg -version` 與 `ffprobe -version`，確認兩者都能顯示版本資訊；若不能，請檢查 FFmpeg 的 `bin` 資料夾是否已加入 PATH。

### 顯示影片超過 60 秒

先在剪輯軟體中把影片裁切為 60 秒內，再重新上傳。這是目前 MVP 為了讓處理時間更容易掌握而設的限制。

### 高品質模式顯示 GPU 或記憶體不足

工具會嘗試使用 CPU fallback（改由 CPU 處理）。CPU 處理較慢，尤其 4K 影片會比 1080p 明顯更久；若仍無法完成，請改用快速模式。

### 偵測不到 Logo 或偵測範圍不正確

請按「新增範圍」手動設定位置與大小，或刪除誤判框後再調整。範圍不要蓋到人物、字幕或需要保留的品牌內容。

### 輸出影片沒有聲音

原始音訊可能無法合併。工具會保留可下載的無聲修復版；請確認 FFmpeg 正常後重試，或在剪輯軟體將原始音訊放回結果影片。

### 4K 影片處理很慢

4K 的每一格畫面都比一般影片大很多，處理時間和記憶體需求會提高。請先用短片測試，並優先使用快速模式。

## 檔案結構

```text
影片去浮水印大師/
├─ app.py                         # 網頁操作介面
├─ start.bat                      # Windows 雙擊啟動檔
├─ requirements-ai.txt            # 選配 LaMa 高品質模式套件
├─ src/watermark_master/          # 影片偵測、修復與輸出功能
└─ tests/                         # 自動測試
```

## 後續可升級方向

- 儲存常用 Logo 範圍，減少每次調整的時間。
- 加入多支影片排程與處理紀錄。
- 針對移動浮水印提供獨立的追蹤模式。
- 改善 4K 與 GPU 的處理效能。
