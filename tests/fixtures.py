"""Synthetic packing slips that mirror TCGplayer's "Ship To:" layout geometry."""
import io
from reportlab.pdfgen import canvas

ORDER_A='ABCD1234-EF5678-9ABCD'
ORDER_B='WXYZ9876-QR5432-10FED'
ADDRESS=['Jane Buyer','42 Test Ave Apt 3','Springfield, IL 62701']

def slip(orders=((ORDER_A,1),),address=ADDRESS,truncate=False):
    """orders: (order id, page count) pairs, rendered as consecutive pages."""
    stream=io.BytesIO()
    c=canvas.Canvas(stream,pagesize=(612,792))
    for oid,total in orders:
        for n in range(1,total+1):
            if n==1:
                c.setFont('Helvetica-Bold',10); c.drawString(36,792-46,'Ship To:')
                c.setFont('Helvetica',10)
                for i,line in enumerate(address): c.drawString(36,792-62-i*12,line)
                c.setFont('Helvetica-Bold',12); c.drawString(36,792-155,f'Order Number: {oid}')
            c.setFont('Helvetica',10); c.drawString(42,792-316,'Quantity  Description')
            c.drawString(36,792-755,f'Order Number: {oid}'); c.drawString(516,792-755,f'Page {n} of {total}')
            c.showPage()
    c.save()
    data=stream.getvalue()
    return data[:-200] if truncate else data
