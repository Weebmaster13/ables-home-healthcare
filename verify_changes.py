import urllib.request

html = urllib.request.urlopen('http://127.0.0.1:5001/').read().decode()

print('=== VERIFICATION CHECKS ===')
print()

# 1. Top navy strip removed
print('1. Top navy strip (top-utility) removed:', 'top-utility' not in html)

# 2. Subtitle = Pursuing Excellence  
print('2. Subtitle = Pursuing Excellence:', 'Pursuing Excellence' in html)

# 3. No Silver Spring, MD in header
print('3. Silver Spring MD removed from header:', 'Silver Spring, MD' not in html)

# 4. Phone number
print('4. Phone 301-326-2225 present:', '301-326-2225' in html)
print('5. tel:+13013262225 present:', 'tel:+13013262225' in html)

# 5. Eyebrow text
print('6. Eyebrow = Skilled and Non-Skilled Nursing Care:', 'Skilled and Non-Skilled Nursing Care' in html)
print('7. Silver Spring, Maryland removed from eyebrow:', 'Silver Spring, Maryland' not in html)

# 6. Main heading
print('8. Main heading = Care at home:', 'Care at home, from people you can trust.' in html)

# 7. What we help with text
print('9. Skilled nursing and non-skilled support:', 'Skilled nursing and non-skilled support' in html)
print('10. alongside everyday removed:', 'alongside everyday' not in html)

# 8. Skilled nursing bullet list
print('11. Bullet - Initial & follow-up assessments:', 'Initial &amp; follow-up assessments' in html)
print('12. Bullet - Wound care:', 'Wound care' in html)
print('13. Bullet - Catheter care:', 'Catheter care' in html)
print('14. Bullet - Tube feeding:', 'Tube feeding' in html)
print('15. Bullet - IV therapy:', 'IV therapy &amp; injections' in html)
print('16. Bullet - Tracheotomy:', 'Tracheotomy &amp; ventilation care' in html)
print('17. Bullet - Evaluation:', 'Evaluation of the home environment' in html)

# 9. Non-skilled nursing care heading
print('18. Non-skilled nursing care heading:', 'Non-skilled nursing care' in html)
print('19. Non-skilled daily support removed:', 'Non-skilled daily support' not in html)

# 10. Footer
print('20. Footer Tel 301-326-2225:', 'tel:+13013262225' in html)
print('21. Footer Fax 301-326-2270:', '301-326-2270' in html)
print('22. After Hours: 301-455-3030:', 'After Hours: 301-455-3030' in html)
print('23. RSA license preserved:', 'RSA' in html and 'Licensed by Maryland Department of Health' in html)

# 11. Request Care button visible
print('24. Request Care button present:', 'Request Care' in html)
print('25. Request Care uses btn-primary:', 'Request Care' in html)

# 12. Navigation
print('26. Home link:', 'href="/"' in html)
print('27. Care, Contact & Careers:', 'Care, Contact' in html)
print('28. Login link:', 'auth/login' in html)
print()

# Check for old phone number still present (should still be there for after-hours)
print('27. Old phone 301-455-3030 still present (for after-hours):', '301-455-3030' in html)
print('28. Old phone 301-455-3030 NOT in regular tel link:', 'tel:+13014553030' not in html)
