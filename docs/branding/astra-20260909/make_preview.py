from pathlib import Path
import json, os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')
from PySide6.QtWidgets import QApplication
from PySide6.QtGui import QImage, QPainter, QColor, QFont, QFontDatabase
from PySide6.QtCore import QRectF, Qt, QByteArray
from PySide6.QtSvg import QSvgRenderer

ROOT = Path(__file__).resolve().parent
app = QApplication([])
font_id = QFontDatabase.addApplicationFont('C:/Windows/Fonts/msyh.ttc')
assert font_id >= 0, 'Microsoft YaHei must load for Chinese preview labels'
FONT_FAMILY = QFontDatabase.applicationFontFamilies(font_id)[0]
items = [
 dict(id='margin',cn='余白',en='Margin',color='#805339',dark='#D6AC8E',tag='为下一次专注，留一分余白。',meaning='余，是尚可使用的额度；白，是无需猜测的工作空间。',logic='一个方整的实体，被一条向外敞开的折形留白切开。实与虚共同构成标识：已用可见，余量也有自己的形状。',trade='气质最安静，也最像独立软件品牌；单看名称，对额度工具的指向较弱。',shape='<path fill="currentColor" d="M5 4h14a1 1 0 0 1 1 1v5h-5V8H8v8h7v-3h5v6a1 1 0 0 1-1 1H5a1 1 0 0 1-1-1V5a1 1 0 0 1 1-1Z"/>'),
 dict(id='yudu',cn='余度',en='Yudu',color='#256A60',dark='#91C8BA',tag='消耗有度，余量有数。',meaning='余度既指剩余的使用空间，也指心中有尺度。Yudu 保留中文读音，适合作为稳定的产品展示名。',logic='两只相向而错位的量规，围出开放的测量空间。横向断口保留呼吸感，避免把额度画成电池或进度圈。',trade='名称与产品关系最直接；折线轮廓较理性，需要依靠留白与比例传达温度。',shape='<path fill="currentColor" d="M4 4h12v3H7v7H4V4Zm16 16H8v-3h9v-7h3v10Z"/>'),
 dict(id='cadra',cn='刻序',en='Cadra',color='#575499',dark='#B8B5EA',tag='看见每一轮工作的节律。',meaning='刻是时间刻度，序是周期与次序。Cadra 是围绕 cadence 与刻度感构造的英文展示名。',logic='两道开口方向相反的圆弧，内外错相，构成一个有停顿的循环。对应短时窗口和周窗口并行，不用指针制造倒计时焦虑。',trade='时间含义最强；16 px 时圆弧较紧密，需通过界面文案补足用量与费用分析的定位。',shape='<path d="M17.5 6.2A8 8 0 1 0 17.5 17.8M9.1 8.8a4.3 4.3 0 1 1 0 6.4" fill="none" stroke="currentColor" stroke-width="2.7" stroke-linecap="round"/>'),
 dict(id='clario',cn='澄量',en='Clario',color='#356688',dark='#9BC6E4',tag='把每一份消耗，看得清楚。',meaning='澄是去除混淆，量是可核对的消耗。Clario 取清晰、明了的语感，是创意展示名。',logic='两片对向的透镜在中间保留一道笔直光隙。外缘柔和、内缘精确，表达从杂乱请求日志中获得清晰读数。',trade='视觉最柔和，适合分析工作台；透镜也可能被理解为阅读或观察工具。',shape='<path fill="currentColor" d="M10.5 3.5C5.8 4.9 3 8 3 12s2.8 7.1 7.5 8.5V3.5Zm3 0v17C18.2 19.1 21 16 21 12s-2.8-7.1-7.5-8.5Z"/>'),
]

def svg(item, color='currentColor'):
    return '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 24 24" fill="none" style="color:'+color+'">'+item['shape'].replace('currentColor',color)+'</svg>'

for item in items:
    for suffix,color in [('mono','currentColor'),('light',item['color']),('dark',item['dark'])]:
        (ROOT/f"{item['id']}-{suffix}.svg").write_text(svg(item,color),encoding='utf-8')

def text(p,x,y,w,h,s,size,color,bold=False):
    f=QFont(FONT_FAMILY,size); f.setBold(bold); p.setFont(f); p.setPen(QColor(color))
    p.drawText(QRectF(x,y,w,h),Qt.AlignmentFlag.AlignLeft|Qt.AlignmentFlag.AlignVCenter,s)

def mark(p,item,x,y,size,color):
    r=QSvgRenderer(QByteArray(svg(item,color).encode('utf-8')))
    assert r.isValid()
    r.render(p,QRectF(x,y,size,size))

img=QImage(1600,1120,QImage.Format.Format_ARGB32);img.fill(QColor('#F5F3EE'))
p=QPainter(img);p.setRenderHint(QPainter.RenderHint.Antialiasing)
text(p,72,34,1400,60,'为 Codexio 设计的新名称与符号',29,'#222A2B',True)
text(p,72,98,1400,35,'GPT-6 Astra · 四套独立品牌提案 · 原生矢量 / 单色成立',13,'#606869')
for i,item in enumerate(items):
    x=72+(i%2)*760;y=178+(i//2)*446
    p.setPen(QColor('#D7D8D1'));p.drawLine(x,y,x+696,y)
    mark(p,item,x+12,y+44,144,item['color'])
    text(p,x+186,y+38,450,55,item['cn'],28,'#222A2B',True)
    text(p,x+188,y+94,450,35,item['en'],17,item['color'])
    text(p,x+188,y+145,475,45,item['tag'],14,'#454F50')
    text(p,x+12,y+218,630,35,'浅底 · 深底 · 16 / 24 / 32 / 64 px',11,'#626B6A')
    for j,size in enumerate([16,24,32,64]):
        xx=x+20+j*94
        mark(p,item,xx,y+282-size/2,size,item['color'])
    p.fillRect(QRectF(x+420,y+260,272,100),QColor('#171D20'))
    for j,size in enumerate([16,24,32,64]):
        mark(p,item,x+438+j*55,y+310-size/2,size,item['dark'])
text(p,72,1060,1450,32,'推荐：余度 / Yudu。所有名称均为创意提案，尚未进行商标、产品名或域名查重。',12,'#606869')
p.end();assert img.save(str(ROOT/'brand-options.png'))

sections=[]
for item in items:
    sections.append(f'''<article style="--accent:{item['color']};--accent-dark:{item['dark']}"><div class="symbol">{svg(item)}</div><div class="words"><h2>{item['cn']} <span>{item['en']}</span></h2><p class="tag">{item['tag']}</p><p>{item['meaning']}</p><p>{item['logic']}</p><p class="trade">取舍：{item['trade']}</p><a href="{item['id']}-mono.svg" download>下载单色 SVG</a></div><div class="actual"><span>实际尺寸</span><div class="actual-mark">{svg(item)}</div><output>32 px</output></div></article>''')
html='''<!doctype html><html lang="zh-CN"><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1"><title>Codexio 品牌提案 · GPT-6 Astra</title><style>
:root{color-scheme:light;--bg:#f5f3ee;--ink:#222a2b;--muted:#596363;--line:#d4d7d0;--size:32px}*{box-sizing:border-box}body{margin:0;background:var(--bg);color:var(--ink);font:16px/1.8 'Microsoft YaHei','Segoe UI',sans-serif}body.dark{color-scheme:dark;--bg:#171d20;--ink:#ebede8;--muted:#b5beba;--line:#424d4e}main{max-width:1264px;margin:auto;padding:52px 48px}h1{font-size:32px;line-height:1.5;margin:0 0 10px;font-weight:600}header>p{color:var(--muted);margin:0;max-width:65ch}nav{display:flex;flex-wrap:wrap;gap:24px;align-items:center;padding:28px 0 30px}label{display:flex;gap:12px;align-items:center}select,button{font:inherit;color:var(--ink);background:var(--bg);border:1px solid var(--line);border-radius:4px;padding:5px 13px;cursor:pointer}button:hover,select:hover{border-color:var(--ink)}:focus-visible{outline:3px solid #598d80;outline-offset:4px}::selection{background:#a7ccbc;color:#172822}.grid{display:grid;grid-template-columns:1fr 1fr;column-gap:64px}article{border-top:1px solid var(--line);padding:34px 0 36px;display:grid;grid-template-columns:112px 1fr;gap:0 25px}.symbol{color:var(--accent);width:112px;height:112px}.symbol svg{width:100%;height:100%}body.dark .symbol,body.dark .actual-mark{color:var(--accent-dark)}h2{margin:0;font-size:26px;font-weight:600;line-height:1.5}h2 span{display:block;font-size:17px;font-weight:400;color:var(--muted)}p{margin:16px 0;line-height:1.8}.tag{margin-top:18px;font-weight:600}.trade{color:var(--muted);font-size:14px}a{color:inherit;text-underline-offset:4px}a:hover{text-decoration-thickness:2px}.actual{grid-column:1/-1;display:flex;align-items:center;gap:26px;min-height:94px;padding-top:20px;color:var(--muted);font-size:13px}.actual-mark{display:flex;width:72px;height:72px;align-items:center;justify-content:center;color:var(--accent)}.actual-mark svg{width:var(--size);height:var(--size)}output{font-variant-numeric:tabular-nums}footer{border-top:1px solid var(--line);padding-top:24px;color:var(--muted)}footer strong{color:var(--ink)}@media(max-width:800px){main{padding:30px 24px}.grid{grid-template-columns:1fr}article{grid-template-columns:84px 1fr;gap:0 18px}.symbol{width:84px;height:84px}h1{font-size:27px}}@media(prefers-reduced-motion:no-preference){button,select{transition:border-color 120ms ease-out}}
</style><main><header><h1>为下一轮专注，设计一个新名字。</h1><p>Codexio 品牌提案，由 GPT-6 Astra 设计。四套符号都从计量、余量与时间出发，使用单色原生矢量，在小尺寸下保留清楚的轮廓。</p></header><nav aria-label="预览设置"><button type="button" id="theme" aria-pressed="false">切换深色背景</button><label>图标尺寸<select id="size"><option>16</option><option>24</option><option selected>32</option><option>64</option></select>px</label></nav><div class="grid">'''+''.join(sections)+'''</div><footer><p><strong>推荐：余度 / Yudu。</strong>名称直接关联剩余额度与合理使用，量规符号在 16 px 仍然保持开放的轮廓，适合常驻桌面、任务栏和深浅两种分析工作台。</p><p>当前仅供选择，不代表已采用新品牌。所有名称、商标及域名均未查重；本工具展示的 API 等价费用不是订阅实际账单。</p><p><a href="brand-options.png">查看 2×2 对比图</a> · <a href="README.md">阅读提案说明</a></p></footer></main><script>
const theme=document.querySelector('#theme');theme.addEventListener('click',()=>{const dark=document.body.classList.toggle('dark');theme.setAttribute('aria-pressed',String(dark));theme.textContent=dark?'切换浅色背景':'切换深色背景'});document.querySelector('#size').addEventListener('change',e=>{document.documentElement.style.setProperty('--size',e.target.value+'px');document.querySelectorAll('output').forEach(o=>o.textContent=e.target.value+' px')});
</script></html>'''
(ROOT/'index.html').write_text(html,encoding='utf-8')
(ROOT/'concepts.json').write_text(json.dumps(items,ensure_ascii=False,indent=2),encoding='utf-8')
print('Generated 12 SVGs, comparison PNG, HTML, and concepts.json')
