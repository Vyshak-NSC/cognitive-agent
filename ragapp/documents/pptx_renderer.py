from __future__ import annotations
from pptx import Presentation
from pptx.util import Inches, Pt
from pptx.enum.text import PP_ALIGN

def _add_title(slide,text):
    box=slide.shapes.add_textbox(Inches(.7),Inches(.45),Inches(11.9),Inches(.75)); p=box.text_frame.paragraphs[0]; p.text=text; p.font.size=Pt(28); p.font.bold=True

def _add_body(slide, lines):
    box=slide.shapes.add_textbox(Inches(.85),Inches(1.45),Inches(11.5),Inches(5.6)); tf=box.text_frame; tf.word_wrap=True; tf.clear()
    for i,(text,level) in enumerate(lines):
        p=tf.paragraphs[0] if i==0 else tf.add_paragraph(); p.text=text; p.level=min(level,3); p.font.size=Pt(17 if level==0 else 15); p.space_after=Pt(8)

def render_pptx(model,path,theme_name='professional',preserve_content=True):
    prs=Presentation(); prs.slide_width=Inches(13.333); prs.slide_height=Inches(7.5); blank=prs.slide_layouts[6]
    title_slide=prs.slides.add_slide(blank); _add_title(title_slide,model.title or 'Presentation')
    current_title=model.title or 'Overview'; lines=[]
    def flush():
        nonlocal lines
        if not lines:return
        # split conservatively to prevent overflow
        for start in range(0,len(lines),7):
            s=prs.slides.add_slide(blank); _add_title(s,current_title); _add_body(s,lines[start:start+7])
        lines=[]
    for b in model.blocks:
        if b.kind=='heading' and (b.level or 1)<=2:
            flush(); current_title=b.text
        elif b.kind in {'paragraph','callout','list_item'}:
            text=b.text.strip()
            if not text: continue
            # long prose is chunked, not silently dropped
            chunks=[text[i:i+520] for i in range(0,len(text),520)]
            for c in chunks: lines.append((c,0 if b.kind=='paragraph' else 1))
            if len(lines)>=7: flush()
        elif b.kind=='table' and b.rows:
            flush(); rows=b.rows; cols=max(len(r) for r in rows); batch=12
            for st in range(0,len(rows),batch):
                part=rows[st:st+batch]; s=prs.slides.add_slide(blank); _add_title(s,current_title)
                table=s.shapes.add_table(len(part),cols,Inches(.7),Inches(1.45),Inches(11.9),Inches(5.4)).table
                for ri,row in enumerate(part):
                    for ci,val in enumerate(row):
                        cell=table.cell(ri,ci); cell.text=str(val); cell.text_frame.paragraphs[0].font.size=Pt(10); cell.text_frame.paragraphs[0].font.bold=(ri==0 and st==0)
    flush(); prs.save(str(path)); return path
