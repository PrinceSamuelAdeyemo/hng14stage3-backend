from django.urls import path
from .views import (
	GitHubOAuthCallbackView,
	GitHubOAuthStartView,
	LogoutView,
	MeView,
	PasswordLoginView,
	PortalView,
	ProfileExportView,
	ProfileListView,
	ProfileSearchView,
	TokenRefreshView,
)

urlpatterns = [
	path('auth/github/start', GitHubOAuthStartView.as_view(), name='github-oauth-start'),
	path('auth/github/callback', GitHubOAuthCallbackView.as_view(), name='github-oauth-callback'),
	path('auth/login', PasswordLoginView.as_view(), name='password-login'),
	path('auth/refresh', TokenRefreshView.as_view(), name='token-refresh'),
	path('auth/logout', LogoutView.as_view(), name='logout'),
	path('me', MeView.as_view(), name='me'),
	path('profiles', ProfileListView.as_view(), name='profile-list'),
	path('profiles/search', ProfileSearchView.as_view(), name='profile-search'),
	path('profiles/export', ProfileExportView.as_view(), name='profile-export'),
	path('portal', PortalView.as_view(), name='portal'),
]
