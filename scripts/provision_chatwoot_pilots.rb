require 'json'
require 'securerandom'

path = '/tmp/salma-provider-access.json'
previous = File.exist?(path) ? JSON.parse(File.read(path)) : {}
result = {}
[['clinic', 'Bright Nile Dental Clinic — Salma demo'], ['garage', 'Harbor Auto Care — Salma demo']].each do |key, name|
  email = "#{key}-owner@salma.invalid"
  user = User.find_by(email: email)
  password = previous.dig(key, 'password')
  unless user
    password = SecureRandom.urlsafe_base64(24) + '!aA1'
    user = User.new(name: name, email: email, password: password, password_confirmation: password)
    user.skip_confirmation!
    user.save!
  end
  account = user.accounts.find_by(name: name)
  unless account
    account = Account.create!(name: name)
    AccountUser.create!(account: account, user: user, role: :administrator)
  end
  token = user.access_token || AccessToken.create!(owner: user)
  result[key] = {account_id: account.id.to_s, api_token: token.token, email: email, password: password}
end
File.write(path, JSON.pretty_generate(result))
File.chmod(0600, path)
puts 'Pilot accounts ready; credentials saved privately.'
