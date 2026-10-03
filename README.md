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
| `assets/sources.json` | من أين تُقتطع خامات كل طبق من صور المنيو (إحداثيات) |
| `assets/textures/` | خامات مستخرجة من الصور (albedo / normal / height) |
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
| `npm run assets` | استخراج الخامات وصور المنتجات والشعار من صور المنيو |
| `npm run models` | بناء كل النماذج في Blender (headless) ثم تحسينها (`-- <slug>` لنموذج واحد) |
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

النماذج تُبنى بـ `blender/build_models.py` من صورة المنيو:

1. **الخامات**: أضف إدخالاً في `assets/sources.json`:
   - بيتزا: `"circle": [cx, cy, r]` (مركز ونصف قطر البيتزا بالبكسل في صورة المنيو) و`"crust"` (عرض الحافة كنسبة). البيتزا الجديدة تُبنى تلقائياً بنفس المولّد.
   - طبق آخر: مستطيل مدوَّر `"top": [cx, cy, w, h, angle]` لسطح الطبق، و`productPhotos` لصورة المنتج.
2. `npm run assets` — يولّد albedo + normal + height + صورة المنتج.
3. لطبق من نوع جديد أضف دالة `build_<type>()` في `blender/build_models.py` وسجّلها في `BUILDERS` (أمثلة جاهزة: صينية، لوح، أوعية، شرائح).
4. `npm run models -- <slug>` (حدّد مسار Blender عبر `BLENDER=...` إن لم يكن في PATH)، أو عبر Blender MCP:
   ```python
   MENU_ROOT = r"X:/LLM/3D-minu"; exec(open(MENU_ROOT + "/blender/build_models.py").read()); build(["<slug>"])
   ```
   ثم `npm run models:optimize -- <slug>`.
5. إطار الكاميرا: افتراضي لكل تصنيف في `config.viewer`، ويمكن تخصيصه لمنتج بحقل `"viewer": { "cameraOrbit": "...", "cameraTarget": "..." }`.

يمكن أيضاً استبدال أي GLB بنموذج أدق (تصوير photogrammetry أو نموذج يدوي) بنفس الاسم `models/<slug>.glb` — المهم: glTF 2.0، الوحدة متر، القاعدة على z=0، ويفضل أقل من 1.5 MB.

### كيف بُنيت النماذج الحالية (وحدودها)
- لكل طبق صورة واحدة فقط من الأعلى، لذلك **النموذج تقريب بصري وليس إعادة بناء مطابقة 100%**. الصفحة تذكر ذلك للزبون.
- البيتزا: قرص بحافة منتفخة بمقطع واقعي (~30 سم، الحجم الحقيقي غير مذكور في المنيو)، الصورة الحقيقية مُسقطة على السطح، و"تضاريس" المكونات تُقدَّر من اختلاف اللون عن الجبن (المكونات ترتفع حتى 6 مم)، ونورمال ماب لتفاصيل السطح، فوق مجداف خشبي كما في الصور (خشبه مأخوذ من الصورة نفسها).
- باقي الأطباق: أشكال إجرائية (صحن، لوح، أوعية، أصابع بطاطا) بألوان وخامات مأخوذة من الصور.
- التحسين: `prune` + `dedup` + `weld` + **KHR_mesh_quantization** (يُفك أصلياً في المتصفح دون تحميل decoder خارجي)، خامات JPEG بأبعاد قوى العدد 2، tangents مُصدّرة، والنتيجة تمر بـ **Khronos glTF Validator بدون أخطاء أو تحذيرات**. البيتزا ≈ 0.62–0.69 MB، المقبلات 0.1–0.3 MB.

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

- **Blender 5.2 + Blender MCP** — بناء كل النماذج (عبر `execute_blender_code` لتشغيل `blender/build_models.py`)، تصدير glTF، ورندر صور poster (EEVEE). لم تتوفر أداة image-to-3D/AI mesh generation، لذلك استُخدم بناء إجرائي + إسقاط الصورة الحقيقية كخامة.
- **@google/model-viewer 4** (مستضاف ذاتياً في `dist/vendor/`) — العارض ثلاثي الأبعاد، التدوير/التكبير باللمس، AR (WebXR / Scene Viewer / Quick Look).
- **glTF-Transform** — التحسين. **gltf-validator** — التحقق.
- **qrcode** — توليد QR. **jsQR + pngjs + jpeg-js** — قراءة QR في التحقق.
- **Python: OpenCV، NumPy، Pillow (مع raqm للعربية)** — الخامات وتركيب المنيو. خطوط ملصقات المنيو: Kristen ITC (خط المنيو الأصلي) وDubai من Windows، مع بدائل في `config.menu`.
- خط الموقع: Baloo Bhaijaan 2 (Google Fonts) مع بديل نظامي.
