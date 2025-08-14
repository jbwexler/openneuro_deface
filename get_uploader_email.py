import requests
import os
import sys

dataset = sys.argv[1]

headers = {
    "Accept-Encoding": "gzip, deflate, br",
    "Content-Type": "application/json",
    "Accept": "application/json",
    "Connection": "keep-alive",
    "DNT": "1",
    "Origin": "https://openneuro.org",
}

query = """
{
    uploader {
        name,
        email
    }
}
""".replace("\n", "")

data = ('{"query":"query testq{dataset(id: \\"%s\\") ' % dataset) + query + '}"}'
response = requests.post("https://openneuro.org/crn/graphql", headers=headers, data=data)
response = response.json()

uploader = response["data"]["dataset"]["uploader"]
name = uploader["name"]
email = uploader["email"]

print(name)
print(email)
