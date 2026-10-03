# pi pizzeria · باي بيتزا — منيو تفاعلي ثلاثي الأبعاد

كل منتج في المنيو له **QR Code خاص به**. عند مسحه بالهاتف تُفتح صفحة المنتج مباشرةً وفيها نموذج 3D يمكن تدويره وتكبيره ومشاهدته من كل الزوايا (ويمكن وضعه على الطاولة بالواقع المعزز AR على الأجهزة الداعمة).

```
Scan QR  →  /product/<slug>/  →  صفحة المنتج (الاسم، 3D، السعر، صورة المنيو)
```

---

## ما الذي يحتويه المشروع

| المسار | المحتوى |
|---|---|
| `images/` | الصور الأصلية كما وصلت — **لا يتم تعديلها أبداً** |
| `menu/original/` | نسخة مطابقة بايت-ببايت من الصور الأصلية بأسماء واضحة (يتحقق `validate` من التطابق) |
| `menu/final/` | المنيو الجديد: كل صفحة مع QR بجانب كل منتج + `pi-pizzeria-menu.pdf` |
| `menu/layout.json` | مكان وحجم كل QR على كل صفحة |
| `products/products.json` | بيانات المنتجات (المصدر الوحيد للحقيقة) + `products.schema.json` |
| `config/site.config.json` | `baseUrl`، مسار صفحة المنتج، الأحجام، التصنيفات، إعدادات QR، إطار الكاميرا |
| `models/<slug>.glb` | النماذج المحسّنة للويب (`models/raw/` = تصدير Blender الخام، `models/source/menu-models.blend` = ملف Blender) |
| `qrcodes/<slug>.png / .svg` | QR لكل منتج (PNG للشاشة، SVG للطباعة) + `manifest.json` |
| `assets/sources.json` | من أين يُؤخذ كل طبق في صور المنيو (إحداثيات، مقياس الصورة بالمتر لكل بكسل) |
| `assets/depth/` | خرائط العمق المقدّرة من الصور (ذاكرة مؤقتة، غير مرفوعة إلى git — تُعاد بـ `npm run depth`) |
| `assets/textures/<slug>/` | خامات كل طبق المستخرجة من الصور (لون، ارتفاع، normal، لوح) + `model.json` (الشكل والأبعاد) |
| `assets/products/`، `assets/posters/`، `assets/brand/` | صورة المنتج، صورة poster للنموذج، شعار π المقتطع من الغلاف |
| `blender/build_models.py` | سكربت Blender الذي يبني كل النماذج |
| `site/` | قوالب الصفحات + CSS + JS |
| `scripts/` | البناء، توليد QR، تركيب المنيو، التحقق، الخادم |
| `dist/` | الموقع النهائي الجاهز للنشر (يُولَّد) |

### المنتجات (16)
- **12 بيتزا** (الأسماء والأسعار صغير/وسط/كبير كما هي مطبوعة): Italian، Margherita، Pepperoni، Khodarji، Ceaser، Burgerys، Chicken Shawerma، Beef Shawerma، Enchilada، Curry Station، Hot Dog، Dynamite.
- **المقبلات الساخنة (3)**: Garlic Bread، Lasagna، Fries. (الصحن الأحمر في نفس الصفحة جزء من تقديم اللازانيا وليس منتجاً مستقلاً.)
- **السلطات**: "Fresh & Tasty Salad / سلطاتنا" — 2,000 IQD للطبق.

### بيانات غير موجودة في المصدر (لم يتم اختلاقها)
- **السلطات**: الصفحة تعرض 12 نوعاً بسعر واحد دون أسماء، لذلك هي منتج واحد. النموذج يعرض اللوح الأول (3 أوعية).
- **الوصف** للبيتزا فارغ لأن المنيو لا يذكر المكونات. الوصف موجود فقط حيث طُبع (Garlic Bread "with cheese"، Lasagna "with cheese & bolognese sauce"، Fries "with special spices").
- ملاحظات في `notes`: سعر Beef Shawerma الصغير (12,000) مؤكَّد من المطعم، تسميات الأحجام العربية في Hot Dog مغطاة جزئياً في الصورة، والأسماء "Ceaser" و"Burgerys" مكتوبة كما طُبعت.

---

## التشغيل

المتطلبات: **Node.js 18+**، **Python 3.10+** مع `opencv-python numpy pillow` (لتجهيز الخامات وتركيب المنيو)، و**Blender 5.x** (مُختبر على 5.2) فقط إذا أردت إعادة بناء النماذج.

```bash
npm install
```

```bash
npm run start
```

افتح `http://localhost:4173`. الخادم يطبع أيضاً عنوان الشبكة المحلية (Wi-Fi) لفتحه من الهاتف.

### تجربة المسح من هاتف حقيقي (نفس شبكة Wi-Fi)
روابط `localhost` لا تعمل من الهاتف، لذا ولّد الـ QR بعنوان جهازك على الشبكة:

```bash
BASE_URL=http://192.168.1.20:4173 npm run all
```

```bash
npm run serve
```

ثم امسح أي QR من `menu/final/` أو من صفحة `/qr/`.

### أوامر npm

| الأمر | الوظيفة |
|---|---|
| `npm run depth` | تقدير عمق كل صورة بـ Depth Anything V2 (يلزم Python + torch، مرة واحدة فقط) |
| `npm run assets` | استخراج الخامات والأشكال وصور المنتجات والشعار من صور المنيو + خرائط العمق |
| `npm run models` | بناء كل النماذج في Blender (headless) ثم تحسينها وتحديث `models/source/menu-models.blend` (`-- <slug>` لنموذج واحد) |
| `npm run models:optimize` | تحسين `models/raw/*.glb` فقط → `models/*.glb` |
| `npm run qr` | توليد QR لكل منتج حسب `BASE_URL` |
| `npm run menu` | تركيب المنيو النهائي + PDF، مع **فك كل QR من الصورة المركّبة والتحقق منه** |
| `npm run build` | بناء الموقع في `dist/` |
| `npm run serve` | خادم محلي لـ `dist/` |
| `npm run validate` | التحقق الكامل (انظر أدناه) — `validate:http` يطلب أيضاً كل رابط QR من الخادم |
| `npm run all` | qr → menu → build → validate |

---

## تغيير `BASE_URL`

الروابط داخل QR هي `baseUrl + productPath`، مثلاً `https://menu.example.com/product/pizza-margherita/`.

- دائماً: عدّل `baseUrl` في `config/site.config.json`، **أو** مرّر متغير البيئة `BASE_URL` (له الأولوية).
- إذا كان الموقع تحت مسار فرعي (مثل GitHub Pages: `https://user.github.io/pi-menu`) ضعه كاملاً في `BASE_URL` وسيتم تعديل كل الروابط تلقائياً.
- بعد التغيير: `npm run all` (يعيد QR والمنيو والموقع ويتحقق). لا حاجة لإعادة بناء النماذج.
- `validate` يفشل إذا كانت QR أو صفحات المنيو قديمة ومولّدة لعنوان مختلف.
- شكل الرابط نفسه قابل للتغيير عبر `productPath` (الافتراضي `/product/{slug}/`).

> قبل طباعة المنيو: ولّد بالدومين النهائي. أي QR مطبوع يبقى صالحاً طالما بقي الدومين وبنية `/product/<slug>/` كما هي.

---

## إضافة منتج جديد

1. أضف عنصراً إلى `products/products.json` (انسخ عنصراً موجوداً):
   ```json
   {
     "id": "pz-13",
     "slug": "pizza-new-name",
     "category": "pizza",
     "menuNumber": 13,
     "name": { "en": "New Name", "ar": "الاسم" },
     "description": { "en": "", "ar": "" },
     "prices": [{ "size": "small", "amount": 10000 }, { "size": "medium", "amount": 12000 }, { "size": "large", "amount": 14000 }],
     "image": "/assets/products/pizza-new-name.jpg",
     "poster": "/assets/posters/pizza-new-name.webp",
     "model": "/models/pizza-new-name.glb",
     "qr": "/qrcodes/pizza-new-name.png",
     "source": { "menuPage": "menu/original/15-pizza-new-name.jpg", "originalFile": "images/<original>.jpg" },
     "placeholders": [],
     "notes": ""
   }
   ```
   - `id` و`slug` يجب أن يكونا فريدين (`validate` يرفض التكرار). أحجام جديدة تُعرّف في `config.sizes`، تصنيفات جديدة في `config.categories`.
   - المعلومة غير المتوفرة: اتركها فارغة وأضف اسم الحقل إلى `placeholders`.
2. ضع صورة المنيو الأصلية في `images/` وانسخها إلى `menu/original/`.
3. أنشئ النموذج (القسم التالي).
4. أضف مكان الـ QR في `menu/layout.json` (صفحة جديدة أو صفحة موجودة).
5. `npm run all`.

لا يلزم أي تعديل على القوالب أو الكود: صفحة `/product/<slug>/` تُولّد تلقائياً من نفس القالب.

## إنشاء نموذج 3D جديد

النماذج تُبنى من صورة المنيو نفسها، في ثلاث خطوات:

1. **المصدر**: أضف إدخالاً في `assets/sources.json`:
   - بيتزا (في `pizzas`): `"circle": [cx, cy, r]` تقريبي للبيتزا بالبكسل و`"crust"` (عرض الحافة / نصف القطر). الحافة الدقيقة واللوح الخشبي وفتحته تُقاس تلقائياً من العمق.
   - طبق آخر (في `dishes`): `"crop": [x, y, w, h]` منطقة الطبق، و`"m_per_px"` (المقياس: كم متراً يساوي البكسل، من شيء معروف الحجم في الصورة)، ثم دالة `do_<dish>()` في `scripts/prepare_textures.py` تصف أجزاءه (طعام بارز `relief`، لوح `board`، وعاء `bowl`) — الأمثلة الموجودة: صحن البطاطا بمقبضه، لوح خبز الثوم الدائري، اللازانيا ولوحها، أوعية السلطة على صينيتها.
2. `npm run depth -- <slug>` ثم `npm run assets` — العمق ثم الخامات و`model.json`.
3. `npm run models -- <slug>` (حدّد مسار Blender عبر `BLENDER=...` إن لم يكن في PATH)، أو عبر Blender MCP:
   ```python
   MENU_ROOT = r"X:/LLM/3D-minu"; exec(open(MENU_ROOT + "/blender/build_models.py").read()); build(["<slug>"])
   ```
   ثم `npm run models:optimize -- <slug>`. لمراجعة النموذج من عدة زوايا: `QA_DIR=<مجلد>` مع أمر Blender يحفظ صوراً من الأعلى والجانب والأمام.
4. إطار الكاميرا: افتراضي لكل تصنيف في `config.viewer`، ويمكن تخصيصه لمنتج بحقل `"viewer": { "cameraOrbit": "...", "cameraTarget": "..." }`.

يمكن أيضاً استبدال أي GLB بنموذج أدق (تصوير photogrammetry أو نموذج يدوي) بنفس الاسم `models/<slug>.glb` — المهم: glTF 2.0، الوحدة متر، القاعدة على z=0، ويفضل أقل من 1.5 MB.

### كيف بُنيت النماذج الحالية (وحدودها)
- كل الأطباق مصوّرة من الأعلى. نموذج **Depth Anything V2** يقدّر عمق كل بكسل، فنحصل على **تضاريس حقيقية من الصورة نفسها**: فقاعات الجبن، الطماطم، مكعبات الجبن، كرات اللحم، أصابع البطاطا واحداً واحداً، انتفاخ الخبز.
- **البيتزا**: حافة البيتزا الحقيقية مقاسة من العمق، حافة عجين مستديرة بمقطع واقعي، جانب العجين مكسو بقشرة البيتزا نفسها (بدون تمطيط)، والمكونات بارزة بارتفاعها الحقيقي تقريباً (القياس مُعاير على طماطم المارجريتا ≈ 3 سم). البيتزا ~30 سم (الحجم غير مذكور في المنيو). اللوح الخشبي مقصوص بشكله الحقيقي من كل صورة، مع فتحة المقبض وظل البيتزا عليه.
- **المقبلات**: صحن البطاطا الأبيض بمقبضه وفتحته، والبطاطا من العمق. خبز الثوم بقطعتيه على لوح دائري (دائرته مقاسة من الصورة؛ الجزء خارج الصورة والمغطى بالكتابة يُكمَل بخشب حقيقي مأخوذ من ألواح البيتزا). اللازانيا: طبقاتها على الجوانب مأخوذة من طبقة البولونيز الظاهرة في الصورة، والجزء المقطوع بحافة الصورة والمغطى بالسهم والكتابة يُكمَل من جبن اللازانيا نفسها. السلطة: ثلاثة أوعية خزفية على الصينية السوداء بشكلها الحقيقي.
- كتابة المنيو والأسهم المرسومة فوق الصور تُكتشف وتُزال من الخامات.
- **النموذج تقريب بصري دقيق وليس مسحاً ثلاثي الأبعاد**: ما لا يظهر في الصورة (أسفل الطبق، الجزء خارج إطار الصورة) مُكمَل. الصفحة تذكر ذلك للزبون.
- التحسين: `prune` + `dedup` + `weld` + quantization + خامات **WebP** + ضغط الهندسة **meshopt** (مفكّكه مستضاف ذاتياً في `vendor/meshopt_decoder.js`). النتيجة تمر بـ **Khronos glTF Validator بدون أخطاء أو تحذيرات**. البيتزا ≈ 0.7–0.85 MB، المقبلات 0.4–0.9 MB.

## إنشاء/تحديث QR

- `npm run qr` يولّد QR لكل المنتجات (PNG 720px + SVG) ويحذف أي QR يتيم لمنتج محذوف.
- مستوى تصحيح الخطأ `Q` (≈25%) مع هامش، اللون الداكن من الشعار `#2b2626` — قابل للتعديل في `config.qr`.
- `npm run menu` يضع QR على الصفحات ويقرأه مجدداً من الصورة المركّبة للتأكد.
- صفحة `/qr/` في الموقع تعرض كل الرموز للطباعة (ملصقات طاولة مثلاً).

## التحقق (`npm run validate`)

يفشل (exit code 1) عند:
- **Missing models / images / posters / QR** أو ملفات مصدر مفقودة.
- **Duplicate slugs / duplicate product IDs / duplicate QR payloads**.
- QR لا يُقرأ، أو يُقرأ لرابط غير رابط منتجه، أو مولّد لـ `BASE_URL` آخر، أو QR يتيم.
- GLB غير صالح (فحص الرأس + Khronos glTF Validator: normals/tangents/accessors/materials).
- **Broken routes**: صفحة منتج غير مبنية، أو لا تحمل نموذجها، أو صفحة قديمة لمنتج محذوف.
- **Broken links**: كل `href/src/poster` محلي في كل صفحات `dist/` (≈300 رابط).
- المنيو النهائي: منتج بلا QR، QR مكرر، QR مطبوع لا يُقرأ أو يشير لمنتج خاطئ، صفحات أقدم من الـ QR.
- نسخ `menu/original` لم تعد مطابقة للأصل في `images/`.

`npm run validate:http` يضيف: طلب كل رابط QR من الخادم الفعلي والتأكد من 200، ثم طلب ملف النموذج المذكور في الصفحة والتأكد من نوعه.

---

## النشر

الموقع static بالكامل. ابنِه محلياً بالدومين النهائي (خطوة المنيو تحتاج Python/OpenCV وخطوط المنيو، وهي غير متوفرة عادةً على خوادم البناء السحابية):

```bash
BASE_URL=https://your-domain npm run all
```

ثم ارفع محتوى `dist/` إلى أي استضافة:

- **Netlify / Cloudflare Pages**: ارفع `dist/` (سحب وإفلات أو CLI). ملف `_headers` يضبط نوع `.glb` والتخزين المؤقت.
- **GitHub Pages**: `BASE_URL=https://<user>.github.io/<repo> npm run all` ثم انشر `dist/` (`.nojekyll` موجود).
- **Vercel / أي خادم (nginx/Apache)**: انشر `dist/`؛ تأكد أن `.glb` يُخدم بنوع `model/gltf-binary` وفعّل gzip/brotli.
- يُستحسن HTTPS (مطلوب لـ AR على بعض الأجهزة).

ثم اطبع `menu/final/pi-pizzeria-menu.pdf` (الـ QR فيه يشير للدومين النهائي).

---

## الأدوات والاعتماديات

- **Blender 5.2 + Blender MCP** — بناء كل النماذج (`blender/build_models.py`، headless أو عبر MCP)، تصدير glTF، ورندر صور poster (EEVEE).
- **Depth Anything V2 Large** (عبر `transformers` + `torch`، يعمل على المعالج) — تقدير العمق من الصور (`scripts/estimate_depth.py`).
- **@google/model-viewer 4** (مستضاف ذاتياً في `dist/vendor/`) — العارض ثلاثي الأبعاد، التدوير/التكبير باللمس، AR (WebXR / Scene Viewer / Quick Look).
- **glTF-Transform + meshoptimizer + sharp** — التحسين (meshopt، WebP). **gltf-validator** — التحقق.
- **qrcode** — توليد QR. **jsQR + pngjs + jpeg-js** — قراءة QR في التحقق.
- **Python: OpenCV، NumPy، SciPy، Pillow (مع raqm للعربية)** — الخامات وتركيب المنيو. خطوط ملصقات المنيو: Kristen ITC (خط المنيو الأصلي) وDubai من Windows، مع بدائل في `config.menu`.
- خط الموقع: Baloo Bhaijaan 2 (Google Fonts) مع بديل نظامي.
