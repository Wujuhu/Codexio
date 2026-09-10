import os,json
from pathlib import Path
os.environ['QT_QPA_PLATFORM']='offscreen'
os.environ['QTWEBENGINE_CHROMIUM_FLAGS']='--disable-gpu'
from PySide6.QtWidgets import QApplication
from PySide6.QtWebEngineWidgets import QWebEngineView
from PySide6.QtCore import QUrl,QTimer
from PySide6.QtGui import QFontDatabase
root=Path(__file__).resolve().parent
app=QApplication([])
QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
view=QWebEngineView();view.resize(1280,1600);view.show()
results={}
def desktop():
    view.grab().save(str(root/'preview-desktop.png'))
    view.page().runJavaScript("JSON.stringify({width:innerWidth,scroll:document.documentElement.scrollWidth,icons:document.querySelectorAll('svg').length})",lambda r:results.update(desktop=json.loads(r)))
    view.resize(390,4000)
    view.page().runJavaScript("document.querySelector('#theme').click();document.querySelector('#size').value='16';document.querySelector('#size').dispatchEvent(new Event('change'))")
    QTimer.singleShot(1200,mobile)
def mobile():
    view.grab().save(str(root/'preview-mobile-dark.png'))
    view.page().runJavaScript("JSON.stringify({width:innerWidth,scroll:document.documentElement.scrollWidth,dark:document.body.classList.contains('dark'),size:getComputedStyle(document.documentElement).getPropertyValue('--size'),labels:[...document.querySelectorAll('output')].map(e=>e.textContent)})",finish)
def finish(r):
    results['mobile']=json.loads(r)
    (root/'verification.json').write_text(json.dumps(results,ensure_ascii=False,indent=2),encoding='utf-8')
    app.quit()
view.loadFinished.connect(lambda ok:QTimer.singleShot(1200,desktop) if ok else app.quit())
view.load(QUrl.fromLocalFile(str(root/'index.html')))
QTimer.singleShot(15000,app.quit)
app.exec()
