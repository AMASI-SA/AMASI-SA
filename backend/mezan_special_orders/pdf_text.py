"""Preserve local IDs in a native Arabic PDF even with an Arabic-only font.

No font file is shipped or modified. ASCII characters unsupported by the selected
Arabic font use ReportLab's standard Helvetica. Arabic shaping stays with the
existing renderer. Used only when a supplier file contains a Mezan-local order.
"""
from reportlab.pdfbase import pdfmetrics
from reportlab.pdfgen.canvas import Canvas


def runs(text, font_name):
    face=pdfmetrics.getFont(font_name).face
    cmap=getattr(face,'charToGlyph',None)
    result=[]
    for char in str(text):
        fallback=bool(cmap is not None and 32<=ord(char)<=126 and not cmap.get(ord(char)))
        font=('Helvetica-Bold' if 'bold' in font_name.casefold() else 'Helvetica') if fallback else font_name
        if result and result[-1][0]==font:
            result[-1]=(font,result[-1][1]+char)
        else:
            result.append((font,char))
    return result


def width(text,font_name,size):
    return sum(pdfmetrics.stringWidth(value,font,size) for font,value in runs(text,font_name))


class LocalTextCanvas(Canvas):
    """Use genuine per-run advances, so fallback glyphs cannot overlap Arabic."""
    def _draw_local(self,x,y,text,*,align=0,mode=None,charSpace=0,direction=None,wordSpace=None):
        parts=runs(text,self._fontname)
        total=width(text,self._fontname,self._fontsize)
        if charSpace:total+=max(0,len(text)-1)*charSpace
        if wordSpace:total+=str(text).count(' ')*wordSpace
        cursor=x-align*total
        original=self._fontname
        for font,value in parts:
            self.setFont(font,self._fontsize)
            super().drawString(cursor,y,value,mode=mode,charSpace=charSpace,direction=direction,wordSpace=wordSpace)
            cursor+=pdfmetrics.stringWidth(value,font,self._fontsize)
            if charSpace:cursor+=len(value)*charSpace
            if wordSpace:cursor+=value.count(' ')*wordSpace
        self.setFont(original,self._fontsize)

    def drawString(self,x,y,text,mode=None,charSpace=0,direction=None,wordSpace=None):
        self._draw_local(x,y,text,mode=mode,charSpace=charSpace,direction=direction,wordSpace=wordSpace)

    def drawRightString(self,x,y,text,mode=None,charSpace=0,direction=None,wordSpace=None):
        self._draw_local(x,y,text,align=1,mode=mode,charSpace=charSpace,direction=direction,wordSpace=wordSpace)

    def drawCentredString(self,x,y,text,mode=None,charSpace=0,direction=None,wordSpace=None):
        self._draw_local(x,y,text,align=0.5,mode=mode,charSpace=charSpace,direction=direction,wordSpace=wordSpace)
