# AI Dashboard

Azərbaycan dilində verilən bank depozit analitikası suallarını etibarlı DuckDB sorğularına çevirən agent əsaslı dashboard tətbiqi. Sistem istifadəçi sualını semantik metrikaya uyğunlaşdırır, yalnız kataloqda icazə verilən filtr və sütunlardan SQL qurur, nəticəni analiz edir və interaktiv görünüş üçün qaytarır.

## Nələr edir

- Təbii dildə (xüsusilə Azərbaycan dilində) analitik suallar qəbul edir.
- 26 semantik unit arasından uyğun metrikaları embedding retrieval və LLM rerank ilə seçir.
- Sorğunu atomik alt-sorğulara ayıra, təkrarlanan semantik sorğuları aradan qaldıra və çoxmetrikalı sorğuları icra edə bilir.
- İcazəli modellər, filterlər və qruplaşdırmalar əsasında parametrli, read-only DuckDB SQL yaradır.
- Aqreqat, pay, müqayisə, breakdown, trend, snapshot trend və reytinqli breakdown sorğularını dəstəkləyir.
- Nəticələri qısa xülasə, insight və cədvəl/qrafik konfiqurasiyası ilə qaytarır.
- React/Vite frontend və FastAPI WebSocket backend təqdim edir.

## Arxitektura

```text
İstifadəçi → React frontend → WebSocket /ws → FastAPI
                                             ↓
  Question Agent → Unit retrieval → Reranker → Parameter inference
                                             ↓
                   Policy validation → SQL compiler → DuckDB (read-only)
                                             ↓
                            Result synthesizer → dashboard JSON → frontend
```

LLM çağırışları OpenAI-compatible Qwen endpoint-i ilə edilir. Hər agent yalnız JSON qaytarmalıdır; natamam JSON cavabları mümkün olduqda təmir edilir.

## Layihə quruluşu

| Yol | Təyinat |
| --- | --- |
| `api.py` | FastAPI tətbiqi, `/health` və `/ws` endpoint-ləri; legacy `web/` səhifəsini `/` ünvanında təqdim edir. |
| `main.py` | Backend üçün Uvicorn başlanğıc nöqtəsi. |
| `agentic_pipeline.py` | Agent orkestrasiya axını, nəticələrin icrası, deduplikasiya və sintez. |
| `agent_prompts.py` | Question, reranker, inference və synthesizer agent prompt-ları. |
| `unit_retrieval.py` | `fastembed` əsaslı semantik unit axtarışı və token uyğunluğu bonusu. |
| `domain_policy.py` | Model və unit müqaviləsinə uyğun filter/group-by doğrulaması. |
| `compiler.py` | YAML model/unit kataloqundan təhlükəsiz DuckDB SQL kompilyasiyası. |
| `units.yml` | Semantik unit kataloqu və onların parametr müqavilələri. |
| `models/` | `sales`, `prolongations`, `portfolio_snapshot`, `balance_movements` model sxemləri. |
| `deposit_real.duckdb` | Lokal analitika verilənlər bazası. |
| `frontend/` | Əsas React + Vite AI Dashboard interfeysi. |
| `web/index.html` | Backend-in birbaşa təqdim etdiyi sadə/legacy interfeys. |
| `tests/test_agentic_pipeline.py` | Pipeline, compiler, policy və multi-unit davranış testləri. |

## Tələblər

- Python 3.10+ (tövsiyə olunur)
- Node.js 18+
- DuckDB verilənlər bazası faylı
- OpenAI-compatible Qwen API açarı və endpoint-i

## Quraşdırma

### 1. Backend mühitini hazırlayın

PowerShell nümunəsi:

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
```

### 2. Mühit dəyişənlərini təyin edin

Layihə kökündə `.env` faylı yaradın. Bu fayl `.gitignore`-dadır və repoya əlavə edilməməlidir.

```env
QWEN_OPENAI_API_KEY=your_api_key
QWEN_OPENAI_API_BASE_URL=https://your-openai-compatible-endpoint/v1
QWEN_OPENAI_MODEL=qwen-plus

DUCKDB_PATH=deposit_real.duckdb
HOST=127.0.0.1
PORT=8000

# İstəyə bağlı: agent prompt/cavab loglarını söndürmək üçün
AGENT_IO_LOGGING=false
```

Qəbul edilən alternativ adlar: `QWEN_API_KEY`, `QWEN_BASE_URL` və `QWEN_MODEL`.

### 3. Backend-i başladın

```powershell
python main.py
```

Backend standart olaraq `http://127.0.0.1:8000` ünvanında işləyir. Bu ünvanda legacy interfeys açılır.

### 4. React frontend-i başladın

Ayrı terminaldə:

```powershell
cd frontend
npm install
npm run dev
```

Vite frontend-i `127.0.0.1:5173` üzərindən açılır və `vite.config.js` vasitəsilə `/ws` və `/health` sorğularını backend-ə yönləndirir.

Production build üçün:

```powershell
cd frontend
npm run build
```

Windows PowerShell execution policy `npm` skriptini bloklayarsa, eyni əmri `npm.cmd run build` və ya `npm.cmd run dev` ilə işə salın.

## Vercel deployment

Repository `vercel.json` ilə iki Vercel Service kimi qurulub:

- `frontend`: `frontend/` qovluğundan Vite build edir və bütün browser səhifələrini təqdim edir.
- `ai-dashboard-api`: `api:app` FastAPI entrypoint-ini işlədir. Yalnız `/health` və `/ws` public rewrites ilə açıqdır.

API açarları və verilənlər bazası frontend-ə ötürülmür. Vercel Project → Settings → Environment Variables bölməsində production/preview üçün server-side dəyişənləri yaradın:

```env
QWEN_OPENAI_API_KEY=...
QWEN_OPENAI_API_BASE_URL=https://your-openai-compatible-endpoint/v1
QWEN_OPENAI_MODEL=your-model-name
DUCKDB_URL=https://private-object-storage.example/deposit_real.duckdb.gz
DUCKDB_URL_TOKEN=optional-bearer-token
DUCKDB_URL_GZIP=true
DUCKDB_URL_BASE64=false
AGENT_IO_LOGGING=false
```

`DUCKDB_URL` private object-storage download URL-si olmalıdır; repository-dəki `deposit_real.duckdb` və `deposit_real.duckdb.gz.b64` `.gitignore` səbəbilə GitHub-a push edilmir. Mövcud `.gz.b64` artefaktını istifadə etdikdə `DUCKDB_URL_GZIP=true` və `DUCKDB_URL_BASE64=true` seçin. `DUCKDB_URL_TOKEN` optional Bearer token-dir. DB ilk `/health` və ya sorğu zamanı function-ın `/tmp` qovluğuna endirilir. URL/token-u `VITE_` prefiksi ilə yaratmayın: həmin dəyişənlər browser bundle-a daxil olur. Vercel Function fayl sistemi müvəqqətidir; hər cold start-da fayl yenidən endirilə bilər.

GitHub repository-ni Vercel-ə import edin. Multi-service import səhifəsində layihə adını `ai_dashboard` saxlaya bilərsiniz; `vercel.json` iki service-in root/build qaydalarını və public rewrites-i təyin edir. `QWEN_OPENAI_API_KEY` dəyərini yalnız Vercel-in Environment Variables UI-sinə daxil edin, chat-a və Git-ə yox.

Service binding yoxdur: browser frontend-dən eyni deployment domain-indəki `/health` və `/ws` public route-larına qoşulur. Backend başqa service-i server-side çağırmır. Vercel WebSocket dəstəkləyir; connection-lar Function müddət limitinə tabedir və frontend reconnect edir. DB-ni Vercel-ə public etməyin; private object storage və məhdudlaşdırılmış download credential istifadə edin.

Vercel build-dən əvvəl lokal yoxlama:

```powershell
cd frontend
npm.cmd ci
npm.cmd run build
```

Backend testləri:

```powershell
python -m unittest discover -s tests -v
```

## API

### `GET /health`

Xidmət və konfiqurasiya vəziyyətini qaytarır:

```json
{
  "status": "ok",
  "llm": "qwen",
  "qwen_configured": true,
  "database_available": true
}
```

### `WS /ws`

Sualı plain text və ya JSON formatında göndərin:

```json
{"question": "Son 6 ayda depozit satışlarının aylıq trendini göstər"}
```

Cavabda `results`, kompilyasiya edilmiş `sql`, `semantic_query`, retrieval/rerank izləri, `synthesis`, `insights` və dashboard komponentləri qaytarılır. Boş və ya düzgün olmayan sorğu `invalid_request`, işləmə xətası isə `query_failed` kodu ilə cavablanır.

## Sorğunun işləmə qaydası

1. **Question Agent** sualı maksimum dörd atomik alt-sorğuya ayırır.
2. **Retriever** `units.yml` kataloqundan ən uyğun beş unit-i seçir.
3. **Reranker Agent** yalnız bu namizədlərdən birini seçir və ya sorğunu rədd edir.
4. **Inference Agent** seçilmiş unit üçün filter, period və `group_by` parametrlərini çıxarır.
5. **Policy və compiler** aliasları normallaşdırır, allowlist-i yoxlayır, default periodları tətbiq edir və SQL yaradır. Doğrulama uğursuz olarsa inference bir dəfə təmir üçün yenidən çağırılır.
6. SQL `deposit_real.duckdb` üzərində `read_only=True` bağlantısı ilə icra edilir.
7. **Synthesizer Agent** yalnız icra nəticələrinə əsaslanan Azərbaycan dilində xülasə və vizuallaşdırma spesifikasiyası yaradır.

## Təhlükəsizlik və məhdudiyyətlər

- Sistem sərbəst SQL qəbul etmir və LLM-in yaratdığı SQL-i birbaşa icra etmir.
- Filter, `group_by`, model sütunları və output-lar unit/model müqaviləsinə görə yoxlanılır.
- Nisbi periodlar limitlidir: maksimum 120 ay və 1095 gün.
- Verilənlər bazası bağlantısı yalnız oxuma rejimində açılır.
- `AGENT_IO_LOGGING=true` olduqda prompt və agent cavabları loglara yazıla bilər. Həssas məlumat olan mühitlərdə bunu `false` saxlayın.
- API açarlarını heç vaxt README, source code və ya commit tarixçəsinə yazmayın. Açar sızması şübhəsi olduqda provider panelindən dərhal yeniləyin.

## Testlər

Backend testlərini layihə kökündə başladın:

```powershell
python -m unittest discover -s tests -v
```

Testlər LLM çağırışlarını mock edir, ancaq bütün icra edilə bilən semantic unit-lərin yaradılan SQL-i lokal DuckDB-də yoxlanır. Buna görə `deposit_real.duckdb` faylı mövcud olmalıdır.

## Konfiqurasiyanı genişləndirmək

Yeni analitika əlavə etmək üçün:

1. Uyğun data modelini `models/` altında təsvir edin və ya mövcud modeli yeniləyin.
2. `units.yml`-ə operation, `based_on`, output və icazəli parametrləri olan unit əlavə edin.
3. Lazımdırsa `compiler.py`-də yeni operation üçün SQL kompilyatoru yazın.
4. Unit üçün compiler/policy testləri əlavə edin.

Unit müqaviləsinin dəqiq saxlanması vacibdir: frontend və agentlər metrikaların icazə verilən filtr, qruplaşdırma və period davranışını məhz bu kataloqdan götürür.
