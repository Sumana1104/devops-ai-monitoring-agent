from locust import HttpUser, between, task

class OpsPilotTraffic(HttpUser):
    wait_time = between(0.05, 0.2)

    @task(5)
    def healthy(self):
        self.client.get("/")

    @task(3)
    def slow(self):
        self.client.get("/slow")

    @task(2)
    def error(self):
        # An intentional 500 is reported as a failed Locust request.
        self.client.get("/error")
