import resend

resend.api_key = "re_GtMbqPvE_5PUKUqKBHwenmwAfycZirxgV"


def send_verification_email(email, token):

    link = f"https://classsync-api-jx3k.onrender.com/auth/verify/{token}"

    resend.Emails.send({
        "from": "ClassSync <onboarding@resend.dev>",
        "to": email,
        "subject": "Verify your ClassSync account",
        "html": f"""
        <h2>Welcome to ClassSync</h2>
        <p>Click below to verify your account.</p>
        <a href="{link}">Verify Account</a>
        """
    })