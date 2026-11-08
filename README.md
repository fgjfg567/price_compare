# بناء الـ APK (مجاناً عبر GitHub)
1. أنشئ مستودعاً جديداً على github.com وارفع كل محتويات هذا المجلد (بما فيها المجلد المخفي .github).
   إن لم يُرفع المجلد المخفي: Add file ← Create new file ← اكتب الاسم  .github/workflows/build.yml  والصق محتوى github_workflow_build.yml
2. تبويب Actions ← Build Android APK ← Run workflow. (يستغرق 10–25 دقيقة)
3. بعد النجاح افتح التشغيل ← Artifacts ← price-compare-apk ← حمّله وفك الضغط ← ثبّت ملف app-release.apk على جوالك
   (يلزم السماح بالتثبيت من مصادر غير معروفة).
إن فشل البناء: انسخ آخر 40 سطراً من سجل Build APK وأرسلها لتصحيحه.
